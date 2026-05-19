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

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from finance_skills_mcp import agent_runner, task_store
from finance_skills_mcp.lock_manager import LockManager
from finance_skills_mcp.skill_catalog import Catalog, seed_catalog
from finance_skills_mcp.task_manager import TaskManager

log = logging.getLogger("finance_skills_mcp.server")


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

    Order matters:
    1. Auth smoke test → ``sys.exit(2)`` on miss (D-12).
    2. ``tasks_root.mkdir(exist_ok=True)``.
    3. ``seed_catalog()``.
    4. ``LockManager`` + ``await lock_mgr.startup_recovery()`` (D-08).
    5. ``TaskManager(... agent_runner_module=agent_runner ...)``.
    6. Yield ``{catalog, lock_mgr, task_mgr}``.
    7. ``await lock_mgr.shutdown()`` on teardown.
    """
    _auth_smoke_test()

    repo_root = Path(__file__).resolve().parents[2]
    tasks_root = repo_root / "tasks"
    tasks_root.mkdir(exist_ok=True)

    catalog: Catalog = seed_catalog()
    lock_mgr = LockManager(tasks_root=tasks_root)
    await lock_mgr.startup_recovery()

    task_mgr = TaskManager(
        catalog=catalog,
        lock_mgr=lock_mgr,
        tasks_root=tasks_root,
        repo_root=repo_root,
        agent_runner_module=agent_runner,
        task_store_module=task_store,
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
