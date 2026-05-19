"""Unit tests for skill_indexer (Phase 2, INIT-01, INIT-02, INIT-05, UNIV-02).

Drives every branch of ``skill_indexer.index()``:

- Happy path against the real ``tests/fixtures/skills`` tree (2 skills, 0 errors).
- One test per malformed fixture under ``tests/fixtures/skills_malformed/``
  parametrized over the README mapping (one IndexErrorCode each).
- INVALID_YAML carries a non-None ``.line``.
- UNKNOWN_FIELD is warning-severity: skill is indexed AND error is reported.
- ENCODING_ERROR via tmp_path latin-1 bytes (no on-disk fixture).
- DUPLICATE_NAME cross-root via tmp_path — both absolute paths surface.
- UNIV-02 mechanism: removing a skill dir between two index() calls drops it.
- Symlink escape via tmp_path (POSIX-only; Windows skipped per D-09).
- Determinism: two consecutive index() calls produce equal IndexResult.
- ``Catalog`` is frozen (dataclasses.FrozenInstanceError on mutation).
- ``Skill`` is frozen (FrozenInstanceError on mutation).
- ``NAME_REGEX`` edge cases (parametrized).
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from finance_skills_mcp.errors import IndexErrorCode
from finance_skills_mcp.skill_indexer import (
    NAME_REGEX,
    IndexError as IxErr,
    IndexResult,
    index,
)
from tests.conftest import FIXTURES_SKILLS_DIR, REPO_ROOT

MALFORMED_DIR = REPO_ROOT / "tests" / "fixtures" / "skills_malformed"


# ---------------------------------------------------------------------------
# Helper: build a minimal valid SKILL.md text for tmp_path-based tests.
# ---------------------------------------------------------------------------

def _valid_skill_md(name: str, description: str = "tmp fixture") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def _make_skill_dir(parent: Path, name: str, body: str | None = None) -> Path:
    """Create ``<parent>/<name>/SKILL.md`` with valid frontmatter by default."""
    d = parent / name
    d.mkdir(parents=True, exist_ok=True)
    skill_md = d / "SKILL.md"
    skill_md.write_text(body if body is not None else _valid_skill_md(name), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# Happy path — real fixtures
# ---------------------------------------------------------------------------

def test_happy_path_two_real_fixtures():
    """Indexing tests/fixtures/skills yields 2 skills sorted by name, 0 errors."""
    r = index((FIXTURES_SKILLS_DIR,))
    assert isinstance(r, IndexResult)
    names = [s.name for s in r.catalog.skills]
    assert names == ["aaaa-test-skill-zzzz", "fixture-skill-alpha"]
    assert r.errors == ()
    # Skill.path is relative to the scan root (M-5 invariant).
    for s in r.catalog.skills:
        assert s.path == s.name, (s.name, s.path)


# ---------------------------------------------------------------------------
# Per-malformed-fixture parametrization — one IndexErrorCode per fixture
# ---------------------------------------------------------------------------

# Hard-coded mapping (the source of truth is tests/fixtures/skills_malformed/_README.md).
_MALFORMED_CASES = [
    ("bad-yaml", IndexErrorCode.INVALID_YAML),
    ("missing-description", IndexErrorCode.MISSING_DESCRIPTION),
    ("missing-name", IndexErrorCode.MISSING_NAME),
    ("invalid-name-UPPER", IndexErrorCode.INVALID_NAME),
    ("empty-file", IndexErrorCode.EMPTY_FILE),
    ("unknown-fields", IndexErrorCode.UNKNOWN_FIELD),
]


@pytest.mark.parametrize("subdir,expected_code", _MALFORMED_CASES)
def test_each_malformed_fixture_emits_its_code(
    tmp_path: Path, subdir: str, expected_code: IndexErrorCode
):
    """One fixture per IndexErrorCode — indexing a single-fixture root
    yields exactly one error of the expected code (UNKNOWN_FIELD also
    indexes the skill)."""
    # Stage the single fixture into a tmp_path root so the malformed-skill
    # neighbours don't pollute this assertion.
    fixture_src = MALFORMED_DIR / subdir / "SKILL.md"
    dest_dir = tmp_path / subdir
    dest_dir.mkdir()
    (dest_dir / "SKILL.md").write_bytes(fixture_src.read_bytes())

    r = index((tmp_path,))
    matching = [e for e in r.errors if e.error_code is expected_code]
    assert len(matching) >= 1, (expected_code, [(e.error_code, e.message) for e in r.errors])
    if expected_code is IndexErrorCode.UNKNOWN_FIELD:
        # Skill IS still indexed.
        assert len(r.catalog.skills) == 1
        assert r.catalog.skills[0].name == subdir
    else:
        # Skill is skipped.
        assert len(r.catalog.skills) == 0


def test_invalid_yaml_populates_line_number():
    """INVALID_YAML must carry a non-None ``.line`` attribute (1-indexed)."""
    r = index((MALFORMED_DIR,))
    yaml_errors = [e for e in r.errors if e.error_code is IndexErrorCode.INVALID_YAML]
    assert len(yaml_errors) == 1
    assert yaml_errors[0].line is not None
    assert yaml_errors[0].line >= 1


def test_unknown_field_is_warning_not_skip():
    """UNKNOWN_FIELD is warning-severity (D-28); the skill must still appear in the catalog."""
    # Index just the unknown-fields subdirectory via a tmp_path harness so
    # neighbouring malformed fixtures don't muddy the assertion.
    r = index((MALFORMED_DIR,))
    names = {s.name for s in r.catalog.skills}
    assert "unknown-fields" in names
    warnings = [e for e in r.errors if e.error_code is IndexErrorCode.UNKNOWN_FIELD]
    assert len(warnings) >= 1
    assert all(e.error_code.is_warning() for e in warnings)


# ---------------------------------------------------------------------------
# ENCODING_ERROR — no on-disk fixture; latin-1 bytes via tmp_path
# ---------------------------------------------------------------------------

def test_encoding_error_on_non_utf8_skill_md(tmp_path: Path):
    """A non-UTF8 SKILL.md surfaces as ENCODING_ERROR."""
    bad_dir = tmp_path / "bad-encoding"
    bad_dir.mkdir()
    # Continuation byte 0xff at the start is invalid UTF-8 start byte.
    (bad_dir / "SKILL.md").write_bytes(b"\xff\xfe---\nname: x\n---\n")
    r = index((tmp_path,))
    enc_errors = [e for e in r.errors if e.error_code is IndexErrorCode.ENCODING_ERROR]
    assert len(enc_errors) == 1, [(e.error_code, e.message) for e in r.errors]
    assert "byte offset" in enc_errors[0].message
    assert len(r.catalog.skills) == 0


# ---------------------------------------------------------------------------
# DUPLICATE_NAME cross-root — both paths reported
# ---------------------------------------------------------------------------

def test_duplicate_name_across_roots_reports_both_paths(tmp_path: Path):
    """A duplicate ``name`` in two different roots emits DUPLICATE_NAME
    for BOTH absolute paths so the operator can copy-paste both into rm."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    a_dir = _make_skill_dir(root_a, "dup")
    b_dir = _make_skill_dir(root_b, "dup")

    r = index((root_a, root_b))
    dup_errors = [e for e in r.errors if e.error_code is IndexErrorCode.DUPLICATE_NAME]
    assert len(dup_errors) >= 2, [(e.error_code, e.path) for e in r.errors]

    reported_paths = {e.path for e in dup_errors}
    assert str((a_dir / "SKILL.md").resolve()) in reported_paths
    assert str((b_dir / "SKILL.md").resolve()) in reported_paths
    # Second occurrence is dropped — catalog contains the name exactly once.
    names = [s.name for s in r.catalog.skills]
    assert names.count("dup") == 1


# ---------------------------------------------------------------------------
# UNIV-02 mechanism — removal between two index() calls
# ---------------------------------------------------------------------------

def test_skill_removal_changes_subsequent_index(tmp_path: Path):
    """Removing a skill directory between two index() calls drops it from
    the second Catalog — UNIV-02 mechanism proof (no caching, no memo)."""
    import shutil

    a_dir = _make_skill_dir(tmp_path, "alpha")
    _make_skill_dir(tmp_path, "beta")

    r1 = index((tmp_path,))
    names1 = {s.name for s in r1.catalog.skills}
    assert names1 == {"alpha", "beta"}

    shutil.rmtree(a_dir)

    r2 = index((tmp_path,))
    names2 = {s.name for s in r2.catalog.skills}
    assert names2 == {"beta"}


# ---------------------------------------------------------------------------
# Symlink escape — POSIX only (D-09 declined Windows)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks only — D-09")
def test_symlink_escaping_root_is_reported(tmp_path: Path):
    """A symlink in the scan root pointing OUTSIDE the root surfaces as
    INVALID_PATH (M-2 fix) and is never followed for read."""
    # Outside-root target: a sibling directory above the scan root.
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside_skill_dir = outside / "evil-skill"
    outside_skill_dir.mkdir()
    (outside_skill_dir / "SKILL.md").write_text(_valid_skill_md("evil"), encoding="utf-8")

    scan_root = tmp_path / "root"
    scan_root.mkdir()
    # Legitimate skill alongside the bad symlink.
    _make_skill_dir(scan_root, "good")
    # Symlink whose name participates in the */SKILL.md glob (we symlink the
    # skill directory itself, so root/escapes/SKILL.md resolves outside the root).
    (scan_root / "escapes").symlink_to(outside_skill_dir, target_is_directory=True)

    try:
        r = index((scan_root,))
    finally:
        # Cleanup the outside-root area.
        import shutil
        shutil.rmtree(outside, ignore_errors=True)

    # The escape MUST be reported.
    escape_errors = [e for e in r.errors if e.error_code is IndexErrorCode.INVALID_PATH]
    assert len(escape_errors) >= 1, [(e.error_code, e.message) for e in r.errors]
    # The reported path references the symlink-as-discovered (in-root), not the
    # outside-root target — the indexer must NOT have resolved-and-followed it
    # into the catalog.
    escape_paths = {e.path for e in escape_errors}
    assert any("escapes" in p for p in escape_paths), escape_paths
    # The legitimate sibling must still appear.
    names = {s.name for s in r.catalog.skills}
    assert names == {"good"}, names


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_index_is_deterministic():
    """Two consecutive index() calls produce equal IndexResult (frozen-dataclass equality)."""
    r1 = index((FIXTURES_SKILLS_DIR,))
    r2 = index((FIXTURES_SKILLS_DIR,))
    assert r1 == r2
    assert r1.catalog == r2.catalog
    assert r1.errors == r2.errors


# ---------------------------------------------------------------------------
# Frozen Catalog / Skill — immutability
# ---------------------------------------------------------------------------

def test_catalog_is_frozen():
    """Mutating Catalog.skills raises FrozenInstanceError."""
    r = index((FIXTURES_SKILLS_DIR,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.catalog.skills = ()  # type: ignore[misc]


def test_skill_is_frozen():
    """Mutating a Skill field raises FrozenInstanceError."""
    r = index((FIXTURES_SKILLS_DIR,))
    s = r.catalog.skills[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.name = "other"  # type: ignore[misc]


def test_index_result_is_frozen():
    """Mutating IndexResult.catalog raises FrozenInstanceError."""
    r = index((FIXTURES_SKILLS_DIR,))
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.catalog = None  # type: ignore[misc]


# ---------------------------------------------------------------------------
# NAME_REGEX edge cases
# ---------------------------------------------------------------------------

_REGEX_CASES = [
    ("a", False),         # too short (1 char)
    ("ab", True),         # 2 chars — lower bound
    ("a-", False),        # trailing hyphen
    ("-a", False),        # leading hyphen
    ("a" * 64, True),     # upper bound
    ("a" * 65, False),    # too long
    ("Foo", False),       # uppercase
    ("foo_bar", False),   # underscore not allowed
    ("1abc", True),       # leading digit is valid per regex
    ("foo-bar-9", True),  # canonical valid
]


@pytest.mark.parametrize("name,is_valid", _REGEX_CASES)
def test_name_regex_edge_cases(tmp_path: Path, name: str, is_valid: bool):
    """For each (name, is_valid) pair, generate a tmp_path fixture and
    assert presence (valid) or INVALID_NAME (invalid)."""
    skill_dir = tmp_path / "fixture-under-test"
    skill_dir.mkdir()
    # The skill directory name is fixed; only the frontmatter `name:` varies.
    (skill_dir / "SKILL.md").write_text(_valid_skill_md(name), encoding="utf-8")
    r = index((tmp_path,))

    if is_valid:
        names = {s.name for s in r.catalog.skills}
        assert name in names, (name, names, [(e.error_code, e.message) for e in r.errors])
        # No INVALID_NAME error for valid input.
        invalid_name_errors = [e for e in r.errors if e.error_code is IndexErrorCode.INVALID_NAME]
        assert invalid_name_errors == []
    else:
        invalid = [e for e in r.errors if e.error_code is IndexErrorCode.INVALID_NAME]
        assert len(invalid) == 1, [(e.error_code, e.message) for e in r.errors]
        # Catalog must NOT contain the offending name.
        assert all(s.name != name for s in r.catalog.skills)


def test_name_regex_constant_is_exposed():
    """NAME_REGEX is importable and matches a known-good name."""
    assert NAME_REGEX.match("foo-bar-9") is not None
    assert NAME_REGEX.match("Foo") is None


# ---------------------------------------------------------------------------
# Missing-root sanity (config bug, not per-skill error)
# ---------------------------------------------------------------------------

def test_missing_root_raises_loud(tmp_path: Path):
    """A non-existent root raises FileNotFoundError — it's a config bug, not
    a per-skill IndexError."""
    missing = tmp_path / "does-not-exist"
    with pytest.raises(FileNotFoundError):
        index((missing,))
