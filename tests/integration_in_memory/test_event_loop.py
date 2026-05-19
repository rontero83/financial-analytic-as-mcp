"""D-21 / EXEC-07 — canonical event-loop hygiene test (200 ms ceiling).

While a 2-second mock agent holds the single-task lock inside
``TaskManager.create``, 20 concurrent ``get_task_status`` calls each MUST
return in well under 200 ms. If any blocking I/O leaked into an ``async def``
tool handler (forgot ``anyio.to_thread.run_sync``, held the
``asyncio.Lock`` during the agent run, etc.) the polls would serialise
behind the agent invocation and individual polls would exceed 200 ms.

This file holds the canonical D-21 / EXEC-07 200 ms hygiene test (per the
01-03-SUMMARY.md deferred-items dedup note and 01-04-PLAN.md Task 1). The
copy that used to live in ``tests/integration_in_memory/test_busy.py``
under the same name is reduced to a 1-line cross-reference comment after
THIS test goes green.

BUSY-probe trick: schedule the slow task via ``asyncio.create_task``,
sleep ~300 ms to let ``status.json`` be written, then fire a synchronous
second ``create_task`` and harvest ``inflight_task_id`` from the BUSY
response. This avoids racing the slow runner to extract the in-flight id.

Reference: 01-04-PLAN.md Task 1; 01-CONTEXT.md D-21 / D-22;
01-01-SUMMARY.md §A5 (verified ``CallToolResult`` attribute names);
01-03-SUMMARY.md Deferred Items #1.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastmcp import Client

from finance_skills_mcp import agent_runner as _agent_runner_module
from finance_skills_mcp.server import mcp

# Verified §A5 attribute names — reuse the canonical helpers from 01-02.
from tests.integration_live._client_helpers import (
    extract_data,
    extract_meta,
    is_error,
)


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_status_polls_stay_under_200ms_during_long_task(monkeypatch):
    """20 concurrent ``get_task_status`` polls during a 2 s task all < 200 ms.

    Canonical D-21 / EXEC-07 hygiene guard. See module docstring for
    rationale and BUSY-probe trick.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-eventloop-canonical-key")

    async def slow_runner(prompt, skills, cwd):
        await asyncio.sleep(2.0)
        return "done"

    monkeypatch.setattr(_agent_runner_module, "run", slow_runner)

    async with Client(mcp) as client:
        running = asyncio.create_task(
            client.call_tool(
                "create_task",
                {"prompt": "long task", "skills": ["fixture-skill-alpha"]},
            ),
            name="long-task-eventloop",
        )

        # Let the lock acquire + initial status.json write complete.
        await asyncio.sleep(0.3)
        assert not running.done(), (
            "slow_runner finished too fast — test cannot exercise the "
            "in-flight window. Increase the sleep in slow_runner."
        )

        # BUSY-probe — harvest in-flight task_id without racing the runner.
        # raise_on_error=False is REQUIRED for the BUSY shape to be visible
        # (FastMCP Client converts isError=True into a ToolError otherwise —
        # per 01-03 finding, this also drops _meta which holds inflight_task_id).
        busy = await client.call_tool(
            "create_task",
            {"prompt": "probe", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )
        assert is_error(busy), (
            f"expected BUSY while long task is in flight; got "
            f"meta={extract_meta(busy)!r} data={extract_data(busy)!r}"
        )
        # verified per 01-01-SUMMARY.md §A5: BUSY response carries
        # ``_meta.inflight_task_id`` (mirrored to ``result.meta`` by FastMCP).
        task_id = extract_meta(busy).get("inflight_task_id")
        assert task_id, f"BUSY response missing inflight_task_id: {busy!r}"

        async def timed_poll() -> float:
            t0 = time.perf_counter()
            r = await client.call_tool(
                "get_task_status",
                {"task_id": task_id},
                raise_on_error=False,
            )
            elapsed = time.perf_counter() - t0
            assert not is_error(r), (
                f"get_task_status errored during long task: "
                f"{extract_meta(r)!r}"
            )
            data = extract_data(r)
            # Status must be ``working`` (NOT ``pending`` or ``running``;
            # those are forbidden vocabulary per MCP 2025-11-25 / D-23).
            assert data.get("status") == "working", (
                f"unexpected status during in-flight poll: {data!r}"
            )
            return elapsed

        durations = await asyncio.gather(*(timed_poll() for _ in range(20)))

        slow = [(i, d) for i, d in enumerate(durations) if d >= 0.2]
        assert not slow, (
            f"Status polls exceeded 200 ms (D-21 / EXEC-07 hygiene contract):\n"
            + "\n".join(f"  poll[{i}] = {d * 1000:.1f} ms" for i, d in slow)
            + f"\nAll durations (ms): "
            + repr([round(d * 1000, 1) for d in durations])
        )

        # Drain the long task cleanly.
        a_result = await running
        assert not is_error(a_result), (
            f"long task errored unexpectedly: {extract_meta(a_result)!r}"
        )
