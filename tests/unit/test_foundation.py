"""Empirical verification of RESEARCH.md §Assumptions Log A1-A7.

If any test in this module fails, update the corresponding implementation in
plan 01-02, 01-03, or 01-04 before proceeding. The §A<N> sections of
01-01-SUMMARY.md record the verified attribute names / patterns / behaviors.

Plan 01-01 Task 1 — runs once to lock down the FastMCP / Claude Agent SDK /
filelock API surfaces before the rest of Phase 1 takes them on faith.
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# A1 — FastMCP stdio entry point
# ---------------------------------------------------------------------------

def test_A1_fastmcp_stdio_entry_point():
    """A1: FastMCP 3.3.1 exposes `run_stdio_async()` on the FastMCP instance.

    [VERIFIED 2026-05-19] FastMCP 3.3.1 has BOTH `run_stdio_async()` AND
    `run_async("stdio")`. The canonical name per CONTEXT.md D-01 is
    `run_stdio_async()` — production code uses that.
    """
    from fastmcp import FastMCP

    mcp = FastMCP(name="probe")
    assert hasattr(mcp, "run_stdio_async"), (
        "FastMCP 3.3.1 should expose run_stdio_async()"
    )
    # Do NOT call it — that would launch a server. Existence check only.


# ---------------------------------------------------------------------------
# A2 — asyncio.create_task as heartbeat fallback
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_A2_asyncio_create_task_heartbeat():
    """A2: A coroutine wrapped in `asyncio.create_task` inside an async context
    can be cancelled cleanly via `task.cancel(); await task`.

    [VERIFIED 2026-05-19] Pattern works. Production lock_manager heartbeat uses
    this idiom per RESEARCH.md Code Examples §lock_manager.py.
    """
    counter = 0

    async def _hb_loop():
        nonlocal counter
        try:
            while True:
                counter += 1
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            raise

    task = asyncio.create_task(_hb_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert counter > 0, "Heartbeat loop should have incremented at least once"


# ---------------------------------------------------------------------------
# A3 — AsyncFileLock(blocking=False) raises filelock.Timeout
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_A3_async_file_lock_timeout(tmp_path: Path):
    """A3: `filelock.AsyncFileLock.acquire(blocking=False)` raises
    `filelock.Timeout` immediately when the lock is held by another instance.

    [VERIFIED 2026-05-19] Behavior confirmed. Note: AsyncFileLock.release() is
    a *coroutine* — production code must `await lock.release()` (different from
    the sync FileLock API).
    """
    from filelock import AsyncFileLock, Timeout

    lock_path = tmp_path / "demo.lock"
    primary = AsyncFileLock(str(lock_path))
    secondary = AsyncFileLock(str(lock_path))

    await primary.acquire()
    try:
        assert primary.is_locked is True
        with pytest.raises(Timeout):
            await secondary.acquire(blocking=False)
    finally:
        await primary.release()

    assert primary.is_locked is False


# ---------------------------------------------------------------------------
# A4 — ToolResult import path + isError wire shape pivot
# ---------------------------------------------------------------------------

def test_A4_tool_result_import_path():
    """A4 (import): `ToolResult` lives at `fastmcp.tools.tool` (NOT `fastmcp`
    top-level, contrary to RESEARCH.md's assumed `from fastmcp import ToolResult`).

    [PIVOT 2026-05-19] RESEARCH.md said `from fastmcp import ToolResult`. The
    actual import path in FastMCP 3.3.1 is `from fastmcp.tools.tool import ToolResult`.
    Also exported via `fastmcp.tools.ToolResult` (alias). Production code uses
    `from fastmcp.tools.tool import ToolResult`.
    """
    with pytest.raises(ImportError):
        from fastmcp import ToolResult  # noqa: F401

    from fastmcp.tools.tool import ToolResult  # noqa: F401

    # ToolResult has: content, structured_content, meta. NO is_error attribute.
    import inspect
    from fastmcp.tools.tool import ToolResult as TR
    sig = inspect.signature(TR)
    params = list(sig.parameters.keys())
    assert "content" in params
    assert "structured_content" in params
    assert "meta" in params
    assert "is_error" not in params, (
        "FastMCP 3.3.1 ToolResult does NOT have is_error — error wire shape "
        "must be produced by subclassing to_mcp_result(). See §A4 of "
        "01-01-SUMMARY.md for the pivot."
    )


@pytest.mark.anyio
async def test_A4_error_tool_result_wire_shape():
    """A4 (wire): Producing `isError: true` + custom `_meta` over the MCP wire
    requires a `ToolResult` subclass that overrides `to_mcp_result()` to build
    a `CallToolResult` with `isError=True`.

    [PIVOT 2026-05-19] FastMCP 3.3.1's `ToolResult.to_mcp_result()` does not
    set isError. Plain `raise ToolError(msg)` loses custom `_meta`. The only
    clean path is subclassing — production `errors.py` defines `ErrorToolResult`.
    Verified via in-memory Client below.
    """
    from fastmcp import FastMCP, Client
    from fastmcp.tools.tool import ToolResult
    from mcp.types import CallToolResult, TextContent

    class _ErrorToolResult(ToolResult):
        def to_mcp_result(self):
            return CallToolResult(
                content=self.content or [],
                structuredContent=self.structured_content,
                isError=True,
                _meta=self.meta or {},
            )

    mcp = FastMCP(name="A4-probe")

    @mcp.tool
    async def busy_tool():
        return _ErrorToolResult(
            content=[TextContent(type="text", text="busy")],
            meta={"inflight_task_id": "abc", "started_at": "2026-05-19T00:00:00Z"},
        )

    async with Client(mcp) as client:
        r = await client.call_tool("busy_tool", {}, raise_on_error=False)
        assert r.is_error is True
        assert r.meta == {
            "inflight_task_id": "abc",
            "started_at": "2026-05-19T00:00:00Z",
        }


# ---------------------------------------------------------------------------
# A5 — fastmcp.Client in-memory test pattern
# ---------------------------------------------------------------------------

def test_A5_in_memory_client_importable():
    """A5: `from fastmcp import Client` succeeds and is a usable class.

    [VERIFIED 2026-05-19] Top-level `Client` export is the FastMCP in-memory
    test client. Used by plans 01-02, 01-04 OPS-04 / D-21 tests.
    """
    from fastmcp import Client

    assert isinstance(Client, type), "fastmcp.Client must be a class"


# ---------------------------------------------------------------------------
# A6 — python-frontmatter parse
# ---------------------------------------------------------------------------

def test_A6_python_frontmatter_importable():
    """A6: `frontmatter.loads()` (or `frontmatter.parse()`) returns a post
    with the YAML frontmatter accessible via `.metadata`.

    [VERIFIED 2026-05-19] Used by UNIV-03 CI grep job. Stays in dev-deps in
    Phase 1; Phase 2 promotes to runtime for the dynamic indexer.
    """
    import frontmatter

    post = frontmatter.loads(
        "---\nname: test\ndescription: example\n---\nbody"
    )
    assert post.metadata.get("name") == "test"
    assert post.metadata.get("description") == "example"


# ---------------------------------------------------------------------------
# A7 — pathlib.Path.is_relative_to availability
# ---------------------------------------------------------------------------

def test_A7_is_relative_to_available():
    """A7: `pathlib.Path.is_relative_to()` is available (Python ≥3.9).
    Project requires ≥3.10 per pyproject.toml.

    [VERIFIED 2026-05-19] Used by task_store.py path-traversal guard (D-05).
    """
    assert Path("/a/b/c").is_relative_to(Path("/a/b")) is True
    assert Path("/a/b/c").is_relative_to(Path("/x/y")) is False
