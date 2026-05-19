"""Disk-walking skill indexer (D-23, D-24, D-28, D-29, D-30, D-34).

Produces the in-memory ``Catalog`` consumed by ``server.app_lifespan`` after
this phase retires the Phase-1 hardcoded seed function in ``skill_catalog``
(D-34, plan 02-03). Pure-function: ``index(roots: tuple[Path, ...]) -> IndexResult``.

Invariants this module pins:

- **Single-level glob only.** Each root is scanned with ``root.glob('*/SKILL.md')``
  — NOT ``**/SKILL.md``. D-30 forbids deep walks: ``scripts`` and ``references``
  come from frontmatter, never from filesystem heuristics. The bounded depth
  also caps the DoS threat from deeply nested SKILL.md trees (T-02-04).
- **``Skill.path`` is resolved RELATIVE TO the scan root that produced the
  entry** — not against ``Path.cwd()`` (M-5 fix from 02-PLAN-CHECK.md). The
  string in ``.skills-index/catalog.json`` is therefore stable across cwd
  changes between unit tests and the FastMCP lifespan launch.
- **``id == name``** (locked by D-29). The validated frontmatter ``name`` is
  used as both the catalog id and the wire ``name`` field.
- **Symlink containment.** Every disk-touching path goes through
  ``Path.resolve(strict=True)`` plus ``is_relative_to(root_resolved)``. A
  symlink whose target escapes the root surfaces as ``INVALID_PATH`` (T-02-01)
  and is never followed for read (M-2 fix from 02-PLAN-CHECK.md — distinct
  code, not a reused INVALID_NAME).
- **Sort-determinism.** The accumulator is sorted by ``Skill.name`` ascending
  before the frozen ``Catalog`` is constructed — UNIV-02 acceptance therefore
  has a stable ordering.

Server-fatal handling (D-32 / D-33) lives in plan 02-03's lifespan wiring;
the indexer just reports.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
import yaml

from finance_skills_mcp.errors import IndexErrorCode
from finance_skills_mcp.skill_catalog import Catalog, Skill

# D-29: 2-64 chars, lowercase letters/digits/hyphens, no leading/trailing hyphen.
# The regex's `[a-z0-9-]*` middle is unbounded, so the length check is enforced
# separately at validation time (`2 <= len(name) <= 64`).
NAME_REGEX: re.Pattern[str] = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")

# WR-01: per-file size cap on SKILL.md. Frontmatter plus a brief description
# fits in well under 1 MiB; anything larger is either accidental (a long-form
# document the author meant to put under references/) or hostile (DoS attempt
# against the read_text call below). Discovered files exceeding this cap
# emit FILE_TOO_LARGE and are skipped without read_text being called.
MAX_SKILL_MD_BYTES: int = 1 * 1024 * 1024  # 1 MiB

# D-28: required + optional whitelist. Any key outside this set emits a
# warning-severity UNKNOWN_FIELD error per offending key, but does NOT
# reject the skill.
_REQUIRED_KEYS: frozenset[str] = frozenset({"name", "description"})
_OPTIONAL_KEYS: frozenset[str] = frozenset({"version", "tags", "scripts", "references"})
_RECOGNIZED_KEYS: frozenset[str] = _REQUIRED_KEYS | _OPTIONAL_KEYS


@dataclass(frozen=True)
class IndexError:  # noqa: A001 — module-scoped name; not the builtin's namespace
    """One entry in ``IndexResult.errors`` — the on-disk D-31 shape.

    ``to_json_dict()`` matches ``errors.json`` exactly: ``line`` / ``hint`` are
    OMITTED when None (not emitted as ``null``).
    """

    path: str
    error_code: IndexErrorCode
    message: str
    line: int | None = None
    hint: str | None = None

    def to_json_dict(self) -> dict[str, Any]:
        # WR-05: surface the warning-vs-error distinction in errors.json so
        # operators don't have to cross-reference the source to know which
        # entries skipped the skill (severity="error") vs which left the
        # skill indexed (severity="warning"). Uses the existing
        # IndexErrorCode.is_warning() predicate to avoid duplicating the
        # mapping. Schema-additive: existing consumers that ignore unknown
        # fields keep working; tests pinning the exact dict shape were
        # updated alongside this change.
        out: dict[str, Any] = {
            "path": self.path,
            "error_code": self.error_code.value,
            "severity": "warning" if self.error_code.is_warning() else "error",
            "message": self.message,
        }
        if self.line is not None:
            out["line"] = self.line
        if self.hint is not None:
            out["hint"] = self.hint
        return out


@dataclass(frozen=True)
class IndexResult:
    """Output of ``index()``: the immutable ``Catalog`` plus the error list.

    Both fields are frozen tuples; the dataclass itself is frozen. Mutating
    either yields ``dataclasses.FrozenInstanceError``.
    """

    catalog: Catalog
    errors: tuple[IndexError, ...] = field(default_factory=tuple)


def _validate_name(name: Any) -> bool:
    """True iff ``name`` matches D-29 regex AND 2 <= len(name) <= 64.

    WR-09 / defense-in-depth: the current ``NAME_REGEX`` pattern
    ``^[a-z0-9][a-z0-9-]*[a-z0-9]$`` already enforces the lower bound
    (a matching string requires BOTH a leading and trailing alnum, so
    ``len(name) >= 2`` is implicit). The explicit ``2 <= len(name)``
    half of the bound check is therefore redundant TODAY but kept
    intentionally so this function stays correct if anyone later
    loosens the regex to allow single-char names (e.g.,
    ``^[a-z0-9][a-z0-9-]*$``). The ``<= 64`` upper bound IS
    load-bearing — the regex's middle ``[a-z0-9-]*`` is unbounded.
    """
    return (
        isinstance(name, str)
        and 2 <= len(name) <= 64
        and NAME_REGEX.match(name) is not None
    )


def _scan_one_root(
    root: Path,
    accumulator: dict[str, tuple[Skill, Path]],
    errors: list[IndexError],
) -> None:
    """Walk a single root, populating ``accumulator`` and ``errors`` in place.

    ``accumulator`` maps validated skill name -> (Skill, resolved-absolute-path
    of the SKILL.md). Duplicate names across roots are detected at the
    accumulator level — both offending entries are reported and the second
    occurrence is DROPPED (server-fatal handling is plan 02-03's concern).
    """
    root_resolved = root.resolve(strict=True)

    # Single-level glob ONLY (D-30 + T-02-04). NEVER `**/SKILL.md`.
    skill_md_paths = sorted(root_resolved.glob("*/SKILL.md"))

    for skill_md_path in skill_md_paths:
        # Symlink containment: resolve and assert is_relative_to(root_resolved).
        # ``strict=True`` ensures the target actually exists (otherwise the glob
        # would have skipped it, but we re-check defensively).
        try:
            resolved = skill_md_path.resolve(strict=True)
        except (OSError, FileNotFoundError) as e:
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.INVALID_PATH,
                    message=f"cannot resolve SKILL.md path: {e}",
                    hint="check that the file exists and is readable",
                )
            )
            continue
        if not resolved.is_relative_to(root_resolved):
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.INVALID_PATH,
                    message=(
                        f"symlink escapes scan root: {skill_md_path} -> {resolved} "
                        f"(root: {root_resolved})"
                    ),
                    hint=(
                        "symlinks pointing outside the configured FSMC_SKILL_ROOTS "
                        "are refused; move the target inside the root or remove the symlink"
                    ),
                )
            )
            continue

        # File-level checks (size, encoding) BEFORE parse.
        try:
            size = resolved.stat().st_size
        except OSError as e:
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.INVALID_PATH,
                    message=f"cannot stat SKILL.md: {e}",
                )
            )
            continue
        if size == 0:
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.EMPTY_FILE,
                    message="SKILL.md is zero bytes",
                    hint="add a YAML frontmatter block with at least `name` and `description`",
                )
            )
            continue
        # WR-01: cap the per-file size BEFORE read_text() allocates the buffer.
        # A 2 GB SKILL.md (real or symlink-target — symlinks are blocked
        # earlier by the containment check) would OOM the server at startup.
        if size > MAX_SKILL_MD_BYTES:
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.FILE_TOO_LARGE,
                    message=(
                        f"SKILL.md is {size} bytes; cap is "
                        f"{MAX_SKILL_MD_BYTES} bytes"
                    ),
                    hint=(
                        "SKILL.md should hold frontmatter + a brief description; "
                        "move long-form content into references/ and link from "
                        "the body"
                    ),
                )
            )
            continue

        try:
            text = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.ENCODING_ERROR,
                    message=f"SKILL.md is not valid UTF-8: byte offset {e.start}: {e.reason}",
                    hint="re-save the file as UTF-8 (no BOM)",
                )
            )
            continue
        except OSError as e:
            # WR-02: PermissionError (a subclass of OSError) was previously
            # uncaught here and propagated out of the scan, taking the
            # entire indexer down for ONE unreadable SKILL.md. Mirror the
            # symmetry of the stat() catch above so a single bad file
            # becomes a per-skill error and the scan continues.
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.IO_ERROR,
                    message=f"cannot read SKILL.md: {e}",
                    hint=(
                        "check the file permissions on SKILL.md and on its "
                        "parent directory (read + execute respectively)"
                    ),
                )
            )
            continue

        # python-frontmatter uses yaml.safe_load by default (T-02-02 mitigation).
        try:
            post = frontmatter.loads(text)
        except yaml.YAMLError as e:
            problem_mark = getattr(e, "problem_mark", None)
            line = (problem_mark.line + 1) if problem_mark is not None else None
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.INVALID_YAML,
                    message=str(e),
                    line=line,
                    hint="repair the YAML frontmatter block (between the leading and trailing `---` lines)",
                )
            )
            continue

        metadata: dict[str, Any] = dict(post.metadata or {})

        # Required keys.
        name = metadata.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.MISSING_NAME,
                    message="frontmatter is missing the required `name` key",
                    hint="add `name: <slug>` to the YAML frontmatter",
                )
            )
            continue
        description = metadata.get("description")
        if not isinstance(description, str) or not description.strip():
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.MISSING_DESCRIPTION,
                    message="frontmatter is missing the required `description` key",
                    hint="add `description: <one-line summary>` to the YAML frontmatter",
                )
            )
            continue

        # Name regex (D-29).
        if not _validate_name(name):
            errors.append(
                IndexError(
                    path=str(skill_md_path),
                    error_code=IndexErrorCode.INVALID_NAME,
                    message=f"name {name!r} violates D-29 regex",
                    hint=(
                        "name must match ^[a-z0-9][a-z0-9-]*[a-z0-9]$ and be 2-64 chars (D-29)"
                    ),
                )
            )
            continue

        # Unknown-field warnings (D-28). The skill IS still indexed.
        for key in sorted(metadata.keys()):
            if key not in _RECOGNIZED_KEYS:
                errors.append(
                    IndexError(
                        path=str(skill_md_path),
                        error_code=IndexErrorCode.UNKNOWN_FIELD,
                        message=f"unrecognized frontmatter key: {key!r}",
                        hint=(
                            f"key {key!r} is not recognized; remove it or "
                            "whitelist it via D-28"
                        ),
                    )
                )

        # Build the Skill entry. ``path`` is the skill DIRECTORY relative to
        # the scan root that produced it (M-5 fix — cwd-independent).
        skill_dir_relative = resolved.parent.relative_to(root_resolved)
        skill = Skill(
            id=name,
            name=name,
            description=description,
            path=str(skill_dir_relative),
        )

        # Duplicate-name detection (cross-root and within-root). The first
        # occurrence wins; the second is reported AND the FIRST is also
        # re-reported so the operator sees BOTH absolute paths.
        existing = accumulator.get(name)
        if existing is not None:
            existing_skill, existing_path = existing
            if existing_path != resolved:
                # Re-emit the prior occurrence on first conflict so operators
                # can copy-paste both paths into `rm`.
                if not any(
                    e.error_code is IndexErrorCode.DUPLICATE_NAME
                    and e.path == str(existing_path)
                    for e in errors
                ):
                    errors.append(
                        IndexError(
                            path=str(existing_path),
                            error_code=IndexErrorCode.DUPLICATE_NAME,
                            message=f"duplicate skill name {name!r}",
                            hint=(
                                "skill name must be unique across all "
                                "FSMC_SKILL_ROOTS (D-32)"
                            ),
                        )
                    )
                errors.append(
                    IndexError(
                        path=str(resolved),
                        error_code=IndexErrorCode.DUPLICATE_NAME,
                        message=f"duplicate skill name {name!r}",
                        hint=(
                            "skill name must be unique across all "
                            "FSMC_SKILL_ROOTS (D-32)"
                        ),
                    )
                )
                # Drop the second occurrence from the accumulator.
                continue

        accumulator[name] = (skill, resolved)


def index(roots: tuple[Path, ...]) -> IndexResult:
    """Walk every ``root`` in order, return the frozen Catalog + collected errors.

    Pre-conditions:
        Roots are expected to exist on disk. Phase-2 code-review fix WR-03
        relaxes the previous "missing root raises FileNotFoundError"
        contract: a missing root now emits a single ``MISSING_ROOT`` entry
        per offending root into ``errors`` and the remaining roots are
        still scanned. Operators thus see ``errors.json`` populated for the
        roots that DID resolve, plus a clean ``MISSING_ROOT`` row pointing
        at the misconfigured entry — instead of zero visibility into the
        offender and zero data for the OTHER roots that worked.

    Returns:
        ``IndexResult(catalog=Catalog(skills=(...sorted by name asc...)),
        errors=(...IndexError tuple...))``. Both the catalog and the errors
        tuple are immutable.

    Notes:
        - Glob is ``*/SKILL.md`` (single level) — D-30.
        - ``Skill.path`` is relative to the root that produced the entry — M-5.
        - Symlinks escaping the root surface as ``INVALID_PATH`` — M-2.
        - Duplicate names across roots emit DUPLICATE_NAME for BOTH paths —
          server-fatal handling is in plan 02-03.
        - Missing roots emit ``MISSING_ROOT`` and DO NOT abort — WR-03.
    """
    accumulator: dict[str, tuple[Skill, Path]] = {}
    errors: list[IndexError] = []

    for root in roots:
        try:
            _scan_one_root(root, accumulator, errors)
        except FileNotFoundError:
            # WR-03: a single missing root used to take down the whole
            # scan, discarding partial results from earlier roots. Now we
            # report it as a per-root MISSING_ROOT error and continue —
            # the empty-catalog D-33 guard at the lifespan layer still
            # catches the "no valid skills anywhere" case.
            errors.append(
                IndexError(
                    path=str(root),
                    error_code=IndexErrorCode.MISSING_ROOT,
                    message=f"scan root does not exist: {root}",
                    hint=(
                        "check FSMC_SKILL_ROOTS for typos and verify the "
                        "directory exists; remove the entry or fix the path"
                    ),
                )
            )

    skills_sorted = tuple(
        skill
        for _, (skill, _path) in sorted(accumulator.items(), key=lambda kv: kv[0])
    )
    return IndexResult(
        catalog=Catalog(skills=skills_sorted),
        errors=tuple(errors),
    )
