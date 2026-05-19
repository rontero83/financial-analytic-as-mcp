"""FastMCP server: lifespan singletons + 4 ``@mcp.tool`` handlers.

Tool handlers route into ``TaskManager``; all blocking I/O lives behind the
``anyio.to_thread.run_sync`` wrappers inside ``TaskManager`` and
``LockManager`` (D-22 / EXEC-07). The auth smoke test (D-12 / OPS-02) runs
FIRST in the lifespan, before any singleton.

Tool annotations per spec:
- ``list_skills`` — readOnlyHint=True, openWorldHint=False
- ``create_task`` — destructiveHint=False, openWorldHint=True
- ``get_task_status`` — readOnlyHint=True, openWorldHint=False
- ``get_task_result`` — readOnlyHint=True, openWorldHint=False
"""
from __future__ import annotations

import collections
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Mapping

import anyio
from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from finance_skills_mcp import agent_runner, task_store
from finance_skills_mcp.errors import IndexErrorCode
from finance_skills_mcp.lock_manager import LockManager
from finance_skills_mcp.skill_catalog import Catalog
from finance_skills_mcp.skill_index_store import INDEX_DIR_NAME, persist_index
from finance_skills_mcp.skill_indexer import IndexResult, index as index_skills
from finance_skills_mcp.task_manager import TaskManager

log = logging.getLogger("finance_skills_mcp.server")


def _parse_skill_roots_env(
    repo_root: Path,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Parse ``FSMC_SKILL_ROOTS`` into a tuple of resolved ``Path`` roots (D-23, D-24).

    The env var is colon-separated (Unix-idiom). Empty / whitespace-only
    segments are dropped. Relative segments are resolved against ``repo_root``.
    Absolute segments are used as-is (then resolved). If the env var is unset
    OR set to the empty string, the default ``"skills"`` is used (D-23 / D-24
    — the nested ``business-investment-advisor/skills/`` tree stays excluded
    unless the operator overrides the env var explicitly).

    Note: ``.resolve()`` is used (NOT ``resolve(strict=True)``) — a missing
    root should surface as ``FileNotFoundError`` from the downstream
    ``skill_indexer.index()`` call so the operator gets the path-context in
    the indexer's exception message, not a bare resolver error here.

    Args:
        repo_root: the repository root that relative roots resolve against.
        env: a mapping to read from (defaults to ``os.environ``). Tests
            inject a controlled ``Mapping`` so they never touch the real
            process environment.

    Returns:
        A tuple of resolved ``Path`` objects. May be a single-element tuple
        (the default) or many.
    """
    if env is None:
        env = os.environ
    raw = env.get("FSMC_SKILL_ROOTS", "")
    if not raw.strip():
        raw = "skills"

    # WR-06 + WR-08: deduplicate scan roots after .resolve() so a config
    # like FSMC_SKILL_ROOTS=skills:./skills or skills:skills/../skills
    # does not silently double-walk the same tree (which would then
    # cascade into per-skill DUPLICATE_NAME-vs-overlap noise inside the
    # indexer). First-occurrence wins so the user-facing order in
    # FSMC_SKILL_ROOTS is preserved for the unique entries. Each dropped
    # duplicate emits a single stderr line so a misconfigured env var is
    # visible to the operator (no new IndexErrorCode needed — the disk
    # walk never observes the duplicate).
    seen: set[Path] = set()
    roots: list[Path] = []
    for segment in raw.split(":"):
        segment = segment.strip()
        if not segment:
            continue
        candidate = Path(segment)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        resolved = candidate.resolve()
        if resolved in seen:
            sys.stderr.write(
                f"finance-skills-mcp: WARNING — duplicate scan root "
                f"{segment!r} (resolves to {resolved}) ignored\n"
            )
            continue
        seen.add(resolved)
        roots.append(resolved)
    # ``raw`` always contains at least the "skills" default token after the
    # blank-string guard above, so ``roots`` is guaranteed non-empty.
    return tuple(roots)


def _auth_smoke_test() -> None:
    """OPS-02 / D-12: verify Anthropic credentials are present before tool registration.

    Reads ``ANTHROPIC_API_KEY`` and ``CLAUDE_CODE_OAUTH_TOKEN``. If neither is
    set, writes a multi-line error to stderr and exits with code 2. Does NOT
    make a real API call — the first task's SDK invocation surfaces invalid
    keys via ``agent_runner`` and ``status.json``.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if api_key or oauth:
        log.info(
            "Auth check passed (%s)",
            "ANTHROPIC_API_KEY" if api_key else "CLAUDE_CODE_OAUTH_TOKEN",
        )
        return

    sys.stderr.write(
        "finance-skills-mcp: NO AUTH CREDENTIALS FOUND\n"
        "  Tried: ANTHROPIC_API_KEY (env var)\n"
        "  Tried: CLAUDE_CODE_OAUTH_TOKEN (env var)\n"
        "  Set one before launching the server.\n"
        "  See: https://code.claude.com/docs/en/authentication\n"
    )
    sys.exit(2)


@asynccontextmanager
async def app_lifespan(server: FastMCP):
    """Construct singletons; run startup recovery; yield lifespan dict.

    Order matters (Phase 2 / D-23, D-32, D-33 wired in):

    1. Auth smoke test — exits non-zero on missing credentials (D-12 / OPS-02).
    2. ``tasks_root.mkdir(exist_ok=True)``.
    3. Parse ``FSMC_SKILL_ROOTS`` (D-23, D-24) via ``_parse_skill_roots_env``.
    4. ``skill_indexer.index(roots)`` produces a frozen ``IndexResult``.
    5. ``persist_index(result, repo_root / INDEX_DIR_NAME)`` — runs ALWAYS,
       even on the fatal paths below, so operators always find a fresh
       ``.skills-index/errors.json`` after a refusal.
    6. Duplicate-name fatal: any ``DUPLICATE_NAME`` entry in
       ``result.errors`` triggers a refusal with BOTH conflicting absolute
       paths written to stderr (D-32 / INIT-03 / SC3).
    7. Empty-catalog fatal: an empty ``result.catalog.skills`` triggers a
       refusal with a per-code ``Counter`` summary written to stderr
       (D-33 / SC4-inverse).
    8. ``LockManager`` + ``await lock_mgr.startup_recovery()`` (D-08).
    9. ``TaskManager(catalog=result.catalog, ...)``.
    10. Yield ``{catalog, lock_mgr, task_mgr}``.
    11. ``await lock_mgr.shutdown()`` on teardown.

    Exit codes are intentionally distinct so the three failure modes are
    distinguishable from the shell: 2 = no auth (D-12), 3 = duplicate skill
    name (D-32), 4 = no valid skills (D-33).
    """
    _auth_smoke_test()

    # WR-07: repo_root derivation MUST work for both editable installs
    # (``<root>/src/finance_skills_mcp/server.py`` — Path(__file__).parents[2]
    # IS the project root) AND wheel installs (``site-packages/finance_skills_mcp/
    # server.py`` — parents[2] is unrelated to the operator's project tree).
    # Env-var override takes precedence; the derivation is the fallback and
    # is sanity-checked by asserting ``<repo_root>/src/finance_skills_mcp/``
    # exists. If neither path identifies a usable root, exit 2 with an
    # actionable stderr message — DO NOT silently mount /usr/lib as repo_root.
    repo_root_env = os.environ.get("FSMC_REPO_ROOT")
    if repo_root_env:
        repo_root = Path(repo_root_env).resolve()
    else:
        derived = Path(__file__).resolve().parents[2]
        if (derived / "src" / "finance_skills_mcp").is_dir():
            repo_root = derived
        else:
            sys.stderr.write(
                f"finance-skills-mcp: cannot locate repo root from "
                f"{__file__!r}; set FSMC_REPO_ROOT explicitly to the "
                f"directory that contains skills/ and tasks/\n"
            )
            sys.exit(2)
    tasks_root = repo_root / "tasks"
    # D-22 / EXEC-07: every blocking I/O call inside an async function
    # (including this lifespan) must hop a worker thread so the asyncio
    # event loop is never blocked. Mirrors the wrapper pattern at
    # task_manager.py:162/196/214 for task_store.atomic_write_* calls.
    await anyio.to_thread.run_sync(lambda: tasks_root.mkdir(exist_ok=True))

    skill_roots = _parse_skill_roots_env(repo_root=repo_root)
    index_dir = repo_root / INDEX_DIR_NAME
    # ``index_skills`` walks each scan root with sync glob/stat/read_text +
    # YAML parsing per SKILL.md — pure CPU + disk, must not block the loop.
    index_result: IndexResult = await anyio.to_thread.run_sync(
        index_skills, skill_roots
    )

    # Persist BEFORE evaluating the fatal guards so the operator always has
    # a fresh errors.json on disk to consult after the process exits.
    # ``persist_index`` does mkdir + 2x atomic_write_json (each: mkstemp,
    # fdopen, write, fsync, os.replace, dir fsync) — wrap in worker thread.
    await anyio.to_thread.run_sync(persist_index, index_result, index_dir)

    # D-32 — duplicate-name fatal. Evaluated BEFORE D-33 because a duplicate
    # could pathologically be the only thing keeping the catalog non-empty;
    # we want the more specific signal to surface first.
    dup_errors = [
        err
        for err in index_result.errors
        if err.error_code is IndexErrorCode.DUPLICATE_NAME
    ]
    if dup_errors:
        sys.stderr.write(
            "finance-skills-mcp: DUPLICATE_NAME — refusing to start (D-32)\n"
            "Conflicting absolute paths:\n"
        )
        for err in dup_errors:
            sys.stderr.write(f"  - {err.path}\n")
        sys.stderr.write(
            f"Full report: {index_dir / 'errors.json'}\n"
        )
        sys.exit(3)

    # D-33 — empty-catalog fatal. If no skill survived validation, the server
    # has nothing to serve. Stderr summarises the per-code Counter of errors
    # so the operator immediately sees the dominant failure mode.
    if len(index_result.catalog.skills) == 0:
        code_counts = collections.Counter(
            err.error_code.value for err in index_result.errors
        )
        sys.stderr.write(
            "finance-skills-mcp: NO VALID SKILLS DISCOVERED — refusing to start (D-33)\n"
            f"Scanned roots: {[str(r) for r in skill_roots]}\n"
            f"Error code counts: {dict(code_counts)}\n"
            f"Full report: {index_dir / 'errors.json'}\n"
        )
        sys.exit(4)

    # Both fatal guards passed — bind the indexer's frozen catalog as the
    # singleton consumed by every MCP tool for the server's lifetime
    # (INIT-04 / SC5 — list_skills never re-scans disk).
    catalog: Catalog = index_result.catalog
    lock_mgr = LockManager(tasks_root=tasks_root)
    await lock_mgr.startup_recovery()

    task_mgr = TaskManager(
        catalog=catalog,
        lock_mgr=lock_mgr,
        tasks_root=tasks_root,
        repo_root=repo_root,
        agent_runner_module=agent_runner,
        task_store_module=task_store,
        skill_roots=skill_roots,
    )

    try:
        yield {
            "catalog": catalog,
            "lock_mgr": lock_mgr,
            "task_mgr": task_mgr,
        }
    finally:
        await lock_mgr.shutdown()


mcp = FastMCP(name="finance-skills-mcp", lifespan=app_lifespan)


# ---------------------------------------------------------------------------
# Helper: extract singletons from the request context. Lifespan_context is the
# dict yielded above; Context.lifespan_context is its accessor.
# ---------------------------------------------------------------------------


def _ctx_catalog(ctx: Context) -> Catalog:
    return ctx.lifespan_context["catalog"]  # type: ignore[index]


def _ctx_task_mgr(ctx: Context) -> TaskManager:
    return ctx.lifespan_context["task_mgr"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# Tools (4)
# ---------------------------------------------------------------------------


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def list_skills(ctx: Context) -> dict:
    """Return the in-memory catalog of available skills.

    Wire shape (per ``specs/list-skills/spec.md``)::

        {"skills": [{"id": str, "name": str, "description": str, "path": str}, ...]}
    """
    catalog = _ctx_catalog(ctx)
    return {"skills": [s.to_wire_dict() for s in catalog.skills]}


@mcp.tool(
    annotations=ToolAnnotations(destructiveHint=False, openWorldHint=True),
)
async def create_task(prompt: str, skills: list[str], ctx: Context):
    """Create one task. Returns ``{task_id}`` on success or a structured error.

    Errors (D-23): ``INVALID_PROMPT``, ``UNKNOWN_SKILL``, ``BUSY``,
    ``STORAGE_ERROR``.

    BUSY shape (D-23 / MCP-05): ``CallToolResult { isError: true, _meta: {
    inflight_task_id, started_at } }``.
    """
    task_mgr = _ctx_task_mgr(ctx)
    return await task_mgr.create(prompt=prompt, skills=skills)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_task_status(task_id: str, ctx: Context):
    """Read ``tasks/<task_id>/status.json``.

    Wire shape on success (per ``specs/get-task-status/spec.md``)::

        {"status": "working"|"completed"|"failed", "elapsed_seconds": float,
         "task_id": str, "started_at": str, ...}

    Error: ``TASK_NOT_FOUND`` (D-23).
    """
    task_mgr = _ctx_task_mgr(ctx)
    return await task_mgr.get_status(task_id=task_id)


@mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_task_result(task_id: str, ctx: Context):
    """Return ``{output_markdown, metadata}`` for a terminal task.

    Errors (D-23 / D-24): ``TASK_NOT_FOUND``, ``TASK_NOT_TERMINAL``.
    Does NOT block — clients poll ``get_task_status`` until terminal.
    """
    task_mgr = _ctx_task_mgr(ctx)
    return await task_mgr.get_result(task_id=task_id)


async def main() -> None:
    """Process entry point: configure logging, start the stdio MCP server.

    Logging goes to stderr only (D-25 — stdout is reserved for MCP JSON-RPC).
    Phase 3 promotes to ``structlog``.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # A1 verified: FastMCP 3.3.1 exposes run_stdio_async().
    await mcp.run_stdio_async()


if __name__ == "__main__":
    anyio.run(main)
