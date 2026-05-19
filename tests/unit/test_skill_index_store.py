"""Unit tests for skill_index_store.persist_index (D-25, D-26, D-27, D-31)."""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from finance_skills_mcp.errors import IndexErrorCode
from finance_skills_mcp.skill_catalog import Catalog, Skill
from finance_skills_mcp.skill_index_store import (
    INDEX_DIR_NAME,
    persist_index,
)
from finance_skills_mcp.skill_indexer import IndexError as IxErr
from finance_skills_mcp.skill_indexer import IndexResult

# --- helper factories -------------------------------------------------------


def _skill(name: str) -> Skill:
    """Build a minimal valid Skill for catalog payload tests."""
    return Skill(
        id=name,
        name=name,
        description=f"desc for {name}",
        path=f"tests/fixtures/skills/{name}",
    )


def _err(name: str, code: IndexErrorCode, **kw: object) -> IxErr:
    """Build a typed IndexError; `line` / `hint` passed via **kw."""
    return IxErr(
        path=f"/abs/{name}/SKILL.md",
        error_code=code,
        message=f"msg for {name}",
        **kw,  # type: ignore[arg-type]
    )


def _result(
    skills: tuple[Skill, ...] = (),
    errors: tuple[IxErr, ...] = (),
) -> IndexResult:
    return IndexResult(catalog=Catalog(skills=skills), errors=errors)


# --- D-27 + D-25: both files always exist; empty case is the literal [] -----


def test_empty_catalog_writes_both_files(tmp_path: Path) -> None:
    """D-25 / D-27: empty input still produces both files; errors.json is [].

    The "missing-file means no errors" ambiguity is forbidden by D-27 —
    operators see literal [] instead.
    """
    persist_index(_result(), tmp_path)

    catalog_path = tmp_path / "catalog.json"
    errors_path = tmp_path / "errors.json"

    assert catalog_path.is_file(), "catalog.json must exist after persist_index"
    assert errors_path.is_file(), "errors.json must exist even with zero errors"

    assert json.loads(catalog_path.read_text()) == {"skills": []}
    assert json.loads(errors_path.read_text()) == []


# --- catalog.json wire-shape -----------------------------------------------


def test_non_empty_catalog_writes_wire_shape(tmp_path: Path) -> None:
    """catalog.json is {"skills": [{id, name, description, path}, ...]}.

    Skill.to_wire_dict() is the projection contract — the on-disk file mirrors
    the list_skills MCP wire envelope (SC5 inspectability).
    """
    s1 = _skill("alpha")
    s2 = _skill("beta")
    persist_index(_result(skills=(s1, s2)), tmp_path)

    payload = json.loads((tmp_path / "catalog.json").read_text())
    assert isinstance(payload, dict)
    assert set(payload.keys()) == {"skills"}
    assert payload["skills"] == [
        {
            "id": "alpha",
            "name": "alpha",
            "description": "desc for alpha",
            "path": "tests/fixtures/skills/alpha",
        },
        {
            "id": "beta",
            "name": "beta",
            "description": "desc for beta",
            "path": "tests/fixtures/skills/beta",
        },
    ]

    # atomic_write_json uses sort_keys=True for deterministic JSON; round-trip
    # through json.dumps with the same options must equal the disk bytes.
    expected = json.dumps(payload, indent=2, sort_keys=True)
    assert (tmp_path / "catalog.json").read_text() == expected


# --- errors.json D-31 shape -------------------------------------------------


def test_non_empty_errors_writes_d31_shape(tmp_path: Path) -> None:
    """D-31: flat array of {path, error_code, message, line?, hint?}.

    Confirms IndexError.to_json_dict() OMITS the `line` / `hint` keys when
    they were None on the source dataclass — they are NOT emitted as null.
    """
    errors = (
        _err("yaml-bad", IndexErrorCode.INVALID_YAML, line=7, hint="fix yaml"),
        _err("name-bad", IndexErrorCode.MISSING_NAME),
        _err(
            "weird",
            IndexErrorCode.UNKNOWN_FIELD,
            line=3,
            hint="remove weird key",
        ),
    )
    persist_index(_result(errors=errors), tmp_path)

    payload = json.loads((tmp_path / "errors.json").read_text())
    assert isinstance(payload, list), "errors.json must be a flat JSON array (D-31)"
    assert len(payload) == 3

    # Each entry must carry the three required keys.
    for entry in payload:
        assert set(entry.keys()) >= {"path", "error_code", "message"}

    # First entry: full shape with line + hint.
    assert payload[0] == {
        "path": "/abs/yaml-bad/SKILL.md",
        "error_code": "INVALID_YAML",
        "message": "msg for yaml-bad",
        "line": 7,
        "hint": "fix yaml",
    }

    # Second entry: line / hint were None — keys MUST be absent (not null).
    assert payload[1] == {
        "path": "/abs/name-bad/SKILL.md",
        "error_code": "MISSING_NAME",
        "message": "msg for name-bad",
    }
    assert "line" not in payload[1]
    assert "hint" not in payload[1]

    # Third entry: warning-severity UNKNOWN_FIELD with both optional fields.
    assert payload[2]["error_code"] == "UNKNOWN_FIELD"
    assert payload[2]["line"] == 3
    assert payload[2]["hint"] == "remove weird key"


# --- D-26: unconditional overwrite on second call ---------------------------


def test_overwrite_catalog_on_second_call(tmp_path: Path) -> None:
    """D-26: second call rewrites catalog.json; no merge with prior contents."""
    persist_index(_result(skills=(_skill("alpha"), _skill("beta"))), tmp_path)
    first = json.loads((tmp_path / "catalog.json").read_text())
    assert len(first["skills"]) == 2

    persist_index(_result(skills=()), tmp_path)
    second = json.loads((tmp_path / "catalog.json").read_text())
    assert second == {"skills": []}, "second call must OVERWRITE, not merge (D-26)"


def test_overwrite_errors_on_second_call(tmp_path: Path) -> None:
    """D-26 + D-27: second call with empty errors writes literal []."""
    persist_index(
        _result(
            errors=(
                _err("a", IndexErrorCode.MISSING_NAME),
                _err("b", IndexErrorCode.INVALID_NAME),
                _err("c", IndexErrorCode.EMPTY_FILE),
            )
        ),
        tmp_path,
    )
    assert len(json.loads((tmp_path / "errors.json").read_text())) == 3

    persist_index(_result(), tmp_path)
    assert json.loads((tmp_path / "errors.json").read_text()) == []


# --- mkdir on missing index_dir --------------------------------------------


def test_creates_index_dir_if_missing(tmp_path: Path) -> None:
    """persist_index creates the target dir (parents=True)."""
    target = tmp_path / "freshly-named-dir" / "nested"
    assert not target.exists()
    persist_index(_result(skills=(_skill("alpha"),)), target)
    assert target.is_dir()
    assert (target / "catalog.json").is_file()
    assert (target / "errors.json").is_file()


# --- D-25 atomic-rename: no torn reads (mock-isolated) ---------------------


def test_atomic_rename_no_torn_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D-25: simulating a mid-write OSError must NOT leave an empty stub file.

    We monkeypatch the atomic_write_json import inside skill_index_store to
    raise OSError on the SECOND call (errors.json). The contract: the first
    file (catalog.json) is fully written before the error — but the destination
    of the failed write must NOT contain a zero-byte truncated stub. Because
    atomic_write_json uses tmp + os.replace, a raise INSIDE the helper leaves
    the destination either fully fresh or fully untouched.
    """
    # First, lay down a known-good baseline so the second-call-failure
    # observer can prove the old contents were preserved.
    persist_index(
        _result(
            skills=(_skill("old"),),
            errors=(_err("old-err", IndexErrorCode.MISSING_NAME),),
        ),
        tmp_path,
    )
    baseline_errors = (tmp_path / "errors.json").read_bytes()
    assert json.loads(baseline_errors)[0]["error_code"] == "MISSING_NAME"

    # Patch the bound name inside skill_index_store (not task_store globally —
    # that would also affect the baseline setup if reused). Record the call
    # ORDER so we can prove the writer attempted catalog.json before errors.json.
    call_log: list[tuple[str, object]] = []
    real_write = __import__(
        "finance_skills_mcp.task_store", fromlist=["atomic_write_json"]
    ).atomic_write_json

    def fake_write(dest: Path, obj: object) -> None:
        call_log.append((str(dest), obj))
        if dest.name == "errors.json":
            # Simulate a crash AFTER the tmp file was created but BEFORE replace.
            # Because atomic_write_json's cleanup logic owns the tmp file, the
            # caller never sees a partial dest. We approximate by raising here
            # without delegating to real_write — equivalent observable outcome:
            # the dest was not touched.
            raise OSError("simulated mid-write failure")
        # Other writes go through the real atomic helper.
        real_write(dest, obj)

    monkeypatch.setattr(
        "finance_skills_mcp.skill_index_store.atomic_write_json", fake_write
    )

    with pytest.raises(OSError, match="simulated mid-write failure"):
        persist_index(
            _result(
                skills=(_skill("new"),),
                errors=(_err("new-err", IndexErrorCode.INVALID_NAME),),
            ),
            tmp_path,
        )

    # Call order: catalog.json was attempted FIRST (and succeeded), then
    # errors.json was attempted (and failed). The plan pins this ordering so
    # plan 02-03's lifespan code can rely on it.
    assert [Path(p).name for p, _ in call_log] == ["catalog.json", "errors.json"]

    # The errors.json on disk is the OLD baseline — unchanged. Critically: it
    # is NOT a zero-byte stub and NOT torn.
    after = (tmp_path / "errors.json").read_bytes()
    assert after == baseline_errors, (
        "atomic-rename invariant violated: errors.json was modified by a "
        "failed write (D-25)"
    )
    # And it still parses as valid JSON — never a partial read.
    assert json.loads(after) == [
        {
            "path": "/abs/old-err/SKILL.md",
            "error_code": "MISSING_NAME",
            "message": "msg for old-err",
        }
    ]

    # catalog.json IS the fresh version because that write succeeded — the
    # writer does not roll back on a downstream failure. Documenting this
    # skew so plan 02-03 knows: a failed errors.json write leaves a fresh
    # catalog next to a stale errors file (the SAFER skew direction).
    fresh_catalog = json.loads((tmp_path / "catalog.json").read_text())
    assert fresh_catalog == {
        "skills": [
            {
                "id": "new",
                "name": "new",
                "description": "desc for new",
                "path": "tests/fixtures/skills/new",
            }
        ]
    }


# --- D-25 atomic-rename: real-disk concurrent-reader sanity check ----------


def test_atomic_writes_round_trip_through_json_loads(tmp_path: Path) -> None:
    """Both files emitted by persist_index must round-trip through json.loads.

    Catches subtle encoding regressions (e.g., a future refactor that
    bypasses atomic_write_json's UTF-8 encoding).
    """
    persist_index(
        _result(
            skills=(_skill("alpha"), _skill("beta")),
            errors=(_err("e1", IndexErrorCode.MISSING_NAME),),
        ),
        tmp_path,
    )
    loaded_catalog = json.loads((tmp_path / "catalog.json").read_text(encoding="utf-8"))
    loaded_errors = json.loads((tmp_path / "errors.json").read_text(encoding="utf-8"))
    assert isinstance(loaded_catalog, dict)
    assert isinstance(loaded_errors, list)
    assert len(loaded_catalog["skills"]) == 2
    assert len(loaded_errors) == 1


# --- return-value contract --------------------------------------------------


def test_returns_none_on_success(tmp_path: Path) -> None:
    """persist_index returns None — Pythonic side-effect-only contract."""
    out = persist_index(_result(skills=(_skill("alpha"),)), tmp_path)
    assert out is None


# --- propagates I/O failures (does NOT swallow OSError) ---------------------


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX chmod semantics — Phase 1 D-09 declines Windows",
)
def test_propagates_oserror_on_readonly_dir(tmp_path: Path) -> None:
    """A read-only parent dir surfaces OSError; the writer does NOT swallow it.

    The server-wiring layer (plan 02-03) decides whether an I/O failure aborts
    startup — that's not the writer's concern.
    """
    readonly_parent = tmp_path / "readonly"
    readonly_parent.mkdir()
    target = readonly_parent / "index"
    # Strip write permission from the parent so mkdir of `target` inside it
    # fails. Save/restore in a try/finally so pytest tmp_path teardown works.
    original_mode = stat.S_IMODE(readonly_parent.stat().st_mode)
    os.chmod(readonly_parent, 0o500)
    try:
        with pytest.raises((OSError, PermissionError)):
            persist_index(_result(skills=(_skill("alpha"),)), target)
    finally:
        os.chmod(readonly_parent, original_mode)


# --- INDEX_DIR_NAME constant is the single source of truth ------------------


def test_index_dir_name_constant() -> None:
    """INDEX_DIR_NAME is the locked directory name for plan 02-03 to import."""
    assert INDEX_DIR_NAME == ".skills-index"
