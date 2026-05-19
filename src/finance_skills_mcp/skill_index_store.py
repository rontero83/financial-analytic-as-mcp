"""Atomic persistence of skill_indexer output (D-25, D-26, D-27, D-31).

Writes ``.skills-index/catalog.json`` and ``.skills-index/errors.json``
side-by-side using the Phase 1 atomic-rename helper from ``task_store``
(``atomic_write_json``). Files are observational — the in-memory frozen
``Catalog`` from ``skill_indexer.index()`` is the runtime source of truth
(specs/init/spec.md, INIT-04).

Invariants this module pins:

- **D-25 (atomic rename).** Every write goes through
  ``task_store.atomic_write_json`` — tmp file in the SAME directory as the
  target, ``fsync(fd)``, ``os.replace``, ``fsync(dirfd)``. Never a naive
  write-mode file handle. A crash mid-write cannot leave a torn JSON
  visible to a reader.
- **D-26 (overwrite unconditionally).** Both files are rewritten on every
  ``persist_index()`` call — no compare-and-skip, no merge with the prior
  contents. The disk scan is the canonical input; these files are the
  rendered output.
- **D-27 (errors.json always written).** ``errors.json`` is ALWAYS written,
  even when ``result.errors`` is empty — as the literal JSON array ``[]``.
  Operators always have a fresh ground truth; "missing file" never means
  "no errors" vs. "stale write".
- **D-31 (errors.json shape).** ``errors.json`` is a flat JSON array of
  ``{path, error_code, message, line?, hint?}`` entries — NOT wrapped in
  an ``{"errors": [...]}`` envelope. ``line`` and ``hint`` are omitted
  (not emitted as ``null``) when the source ``IndexError`` had them as
  ``None`` — that's the contract from ``IndexError.to_json_dict()``.

On-disk shape of ``catalog.json``:

    {"skills": [{"id": str, "name": str, "description": str, "path": str}, ...]}

This wraps the wire-skills list in a single-key object so the on-disk file
mirrors the ``list_skills`` MCP-tool response envelope (SC5 inspectability:
operators reading ``catalog.json`` see the same projection clients see).
``specs/init/spec.md`` line 92 ("JSON array of N skill entries") describes
the array loosely; the locked Phase 1 wire envelope is ``{"skills": [...]}``
and this writer follows the wire envelope.

The ``INDEX_DIR_NAME`` constant exists so plan 02-03's server wiring and the
unit suite reference one source of truth, not a duplicated string literal.
"""
from __future__ import annotations

from pathlib import Path

from finance_skills_mcp.skill_indexer import IndexResult
from finance_skills_mcp.task_store import atomic_write_json

__all__ = ["INDEX_DIR_NAME", "persist_index"]

# Single source of truth for the on-disk index directory name. The server
# wiring in plan 02-03 imports this constant rather than hard-coding the
# string literal again — keeps the location of ``.skills-index/`` discoverable
# from one place.
INDEX_DIR_NAME: str = ".skills-index"


def persist_index(result: IndexResult, index_dir: Path) -> None:
    """Atomically write ``catalog.json`` and ``errors.json`` into ``index_dir``.

    Both files are overwritten unconditionally (D-26). ``errors.json`` is
    written even when ``result.errors`` is empty — as the literal ``[]`` JSON
    array (D-27). Writes are performed via ``task_store.atomic_write_json``
    (D-25 atomic-rename pattern); a crash between the two writes can leave a
    fresh ``catalog.json`` next to a stale ``errors.json`` (or vice versa),
    but each individual file is always either fully fresh or fully untouched
    — never torn.

    Args:
        result: the frozen ``IndexResult`` from ``skill_indexer.index()``.
            ``result.catalog.skills`` is the list of validated ``Skill``s;
            ``result.errors`` is the tuple of typed ``IndexError`` entries
            (may be empty).
        index_dir: the directory to write into (typically
            ``repo_root / INDEX_DIR_NAME``). Created with ``parents=True``
            if it does not yet exist; an existing directory is left alone.

    Returns:
        None. ``OSError`` / ``PermissionError`` raised by the underlying
        atomic writer propagate; the writer does NOT swallow I/O errors —
        deciding what to do about them (abort startup vs. continue) is plan
        02-03's server-wiring concern (D-33).
    """
    index_dir = Path(index_dir)
    # parents=True so a fresh ``.skills-index/`` under a missing prefix works;
    # exist_ok=True because we're called once per server startup and the dir
    # usually persists across runs.
    index_dir.mkdir(parents=True, exist_ok=True)

    # catalog.json — wrap the wire skills in {"skills": [...]} to mirror the
    # list_skills tool response envelope. Skill.to_wire_dict already produces
    # the canonical {id, name, description, path} projection — do not
    # duplicate that logic here.
    catalog_payload: dict[str, list[dict]] = {
        "skills": [skill.to_wire_dict() for skill in result.catalog.skills],
    }

    # errors.json — flat JSON array per D-31. Empty tuple → literal [] (D-27).
    # IndexError.to_json_dict() omits line/hint when None, so the on-disk
    # shape varies per entry — that's the locked schema.
    errors_payload: list[dict] = [err.to_json_dict() for err in result.errors]

    # Order: catalog first, then errors. If a crash falls between the two
    # writes, the operator sees a fresh catalog next to a stale errors file —
    # which is the SAFER skew (the catalog is what list_skills replays; the
    # errors file is observational). The unit suite pins this order so plan
    # 02-03's lifespan code can rely on it.
    atomic_write_json(index_dir / "catalog.json", catalog_payload)
    atomic_write_json(index_dir / "errors.json", errors_payload)
