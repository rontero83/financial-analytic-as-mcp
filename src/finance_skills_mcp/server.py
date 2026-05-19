"""FastMCP server: lifespan construction of singletons + 4 @mcp.tool stubs.

Task 2a of plan 01-01 lands the **lifespan shell + tool stubs only** — the
four ``@mcp.tool`` bodies raise ``NotImplementedError`` and Task 2b wires
them to the ``TaskManager``. The auth smoke test (D-12 / OPS-02) is fully
implemented here and runs FIRST inside the lifespan, before any singleton.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from fastmcp import FastMCP

from finance_skills_mcp import agent_runner, task_store
from finance_skills_mcp.lock_manager import LockManager
from finance_skills_mcp.skill_catalog import Catalog, seed_catalog
from finance_skills_mcp.task_manager import TaskManager

log = logging.getLogger("finance_skills_mcp.server")


def _auth_smoke_test() -> None:
    """OPS-02 / D-12: verify Anthropic credentials are present BEFORE registering tools.

    Reads ``ANTHROPIC_API_KEY`` and ``CLAUDE_CODE_OAUTH_TOKEN`` from the
    environment. If neither is set, write a multi-line error to ``stderr``
    naming the methods tried and exit with code 2. Does **NOT** make a real
    API call (cost + latency + breaks offline ``--help``); the first task's
    SDK invocation surfaces invalid keys via ``agent_runner`` and
    ``status.json``.
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
    """Server lifespan (D-12, D-13, D-25):

    1. Auth smoke test → ``sys.exit(2)`` if credentials missing.
    2. ``tasks_root.mkdir(exist_ok=True)``.
    3. ``seed_catalog()`` → frozen one-entry catalog (D-14).
    4. ``LockManager(...)`` + ``await lock_mgr.startup_recovery()``.
    5. ``TaskManager(...)``.
    6. Yield ``{catalog, lock_mgr, task_mgr}`` for tool handlers.
    7. ``await lock_mgr.shutdown()`` on teardown.
    """
    _auth_smoke_test()  # fail-fast BEFORE constructing singletons

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
# Tool stubs (Task 2a). Task 2b replaces these bodies with real orchestration.
# ---------------------------------------------------------------------------


@mcp.tool
async def list_skills() -> dict:
    """Return the in-memory catalog of available skills.

    Task 2a stub — wired in Task 2b.
    """
    raise NotImplementedError("list_skills — wired in Task 2b")


@mcp.tool
async def create_task(prompt: str, skills: list[str]) -> dict:
    """Create a new task. Returns ``{task_id}`` on success, BUSY on contention.

    Task 2a stub — wired in Task 2b.
    """
    raise NotImplementedError("create_task — wired in Task 2b")


@mcp.tool
async def get_task_status(task_id: str) -> dict:
    """Read ``tasks/<task_id>/status.json``.

    Task 2a stub — wired in Task 2b.
    """
    raise NotImplementedError("get_task_status — wired in Task 2b")


@mcp.tool
async def get_task_result(task_id: str) -> dict:
    """Return ``{output_markdown, metadata}`` for a terminal task.

    Task 2a stub — wired in Task 2b.
    """
    raise NotImplementedError("get_task_result — wired in Task 2b")


async def main() -> None:
    """Process entry point: configure logging, start the stdio MCP server.

    Logging goes to stderr only (D-25 — stdout is reserved for the MCP
    JSON-RPC protocol on stdio transport). Phase 3 promotes to ``structlog``.
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
