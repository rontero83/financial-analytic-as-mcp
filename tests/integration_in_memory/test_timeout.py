"""Hard task timeout + clean lock release (D-11, EXEC-06).

Two guarantees this file proves empirically:

1. **Hard timeout terminates the agent and writes failed:timeout.** With
   ``FSM_TASK_TIMEOUT_SECONDS = 2`` and a mock agent that sleeps for 10 s,
   the task is cancelled at the 2 s mark; ``output.md`` and ``status.json``
   land per EXEC-04 ordering with ``status == "failed"`` and
   ``error_reason == "timeout"``.

2. **The lock is released after a timeout.** A *subsequent* ``create_task``
   issued after the timeout-failed task succeeds — proving the timeout's
   ``finally`` branch released the asyncio.Lock + AsyncFileLock + heartbeat,
   and tore down ``tasks/.lock`` cleanly.

The timeout test uses ``monkeypatch.setattr`` (NOT ``setenv``) because
``TASK_TIMEOUT_SECONDS`` is captured at module import time as a module-level
constant (``finance_skills_mcp.task_manager.TASK_TIMEOUT_SECONDS``).
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastmcp import Client

from finance_skills_mcp import agent_runner as _agent_runner_module
from finance_skills_mcp import task_manager as _task_manager_module
from finance_skills_mcp.server import mcp
from tests.integration_live._client_helpers import (
    extract_data,
    extract_meta,
    is_error,
)


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_task_timeout_marks_failed(monkeypatch):
    """A task exceeding TASK_TIMEOUT_SECONDS lands with status=failed, error_reason=timeout.

    Mock runner sleeps 10 s; timeout fires at 2 s. The create_task call
    returns AFTER the timeout fires (single-task synchronous contract) with
    a *task_id*, and status.json on disk reflects the timeout.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-timeout-test-key")
    # Phase 2 / D-23: route the indexer at the test-fixtures skill root so
    # ``fixture-skill-alpha`` appears in the catalog (D-34 retired the seed).
    monkeypatch.setenv("FSMC_SKILL_ROOTS", "tests/fixtures/skills")
    # TASK_TIMEOUT_SECONDS is a module-level constant captured at import time;
    # we override it directly. Float to match the asyncio.wait_for contract.
    monkeypatch.setattr(_task_manager_module, "TASK_TIMEOUT_SECONDS", 2.0)

    async def slow_runner(prompt, skills, cwd):
        await asyncio.sleep(10.0)
        return "never reached"

    monkeypatch.setattr(_agent_runner_module, "run", slow_runner)

    async with Client(mcp) as client:
        t0 = time.perf_counter()
        result = await client.call_tool(
            "create_task",
            {"prompt": "this will time out", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )
        elapsed = time.perf_counter() - t0

        # The task itself is timed out but create_task still returns a success
        # envelope with the task_id (the agent failure surfaces in status.json,
        # not as a tool-call-level error per D-11 / EXEC-06).
        assert not is_error(result), (
            f"create_task on a timeout-bound task should still return task_id "
            f"(not isError=true): meta={extract_meta(result)!r}"
        )
        data = extract_data(result)
        task_id = data.get("task_id")
        assert task_id is not None, f"create_task missing task_id: {data!r}"

        # Timeout fired in roughly 2 s — give a generous ceiling for I/O.
        assert elapsed < 5.0, (
            f"create_task took {elapsed:.2f}s — expected ~2s + epsilon "
            f"(timeout did not fire cleanly?)"
        )
        # And not absurdly quick — proves the timeout actually ran.
        assert elapsed >= 1.5, (
            f"create_task returned after only {elapsed:.2f}s — timeout did not "
            f"hold for ~2s? Mock runner setup is suspect."
        )

        # status.json must show failed:timeout.
        st = await client.call_tool(
            "get_task_status", {"task_id": task_id}, raise_on_error=False
        )
        assert not is_error(st), (
            f"get_task_status returned error after timeout: {extract_meta(st)!r}"
        )
        st_data = extract_data(st)
        assert st_data.get("status") == "failed", (
            f"status after timeout should be 'failed' (not {st_data.get('status')!r})"
        )
        assert st_data.get("error_reason") == "timeout", (
            f"status.error_reason should be 'timeout' (got "
            f"{st_data.get('error_reason')!r})"
        )

        # And output.md should mention the timeout.
        result_payload = await client.call_tool(
            "get_task_result", {"task_id": task_id}, raise_on_error=False
        )
        assert not is_error(result_payload)
        gr = extract_data(result_payload)
        output_md = gr.get("output_markdown", "")
        assert "timeout" in output_md.lower(), (
            f"output.md should mention 'timeout' (got: {output_md[:200]!r})"
        )


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_lock_released_after_timeout(monkeypatch):
    """After a timeout-failed task, a subsequent create_task succeeds.

    Proves the timeout branch's ``finally: await lock_mgr.release()`` actually
    fires (asyncio.Lock release + AsyncFileLock release + tasks/.lock unlink +
    heartbeat cancel). Without it the next call would BUSY indefinitely.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-timeout-release-test-key")
    # Phase 2 / D-23: route the indexer at the test-fixtures skill root.
    monkeypatch.setenv("FSMC_SKILL_ROOTS", "tests/fixtures/skills")
    monkeypatch.setattr(_task_manager_module, "TASK_TIMEOUT_SECONDS", 2.0)

    call_count = {"n": 0}

    async def runner(prompt, skills, cwd):
        call_count["n"] += 1
        if call_count["n"] == 1:
            await asyncio.sleep(10.0)  # first call: forces timeout
            return "unreachable"
        # second call: returns immediately
        return f"FIXTURE-ECHO::{prompt}::END-FIXTURE"

    monkeypatch.setattr(_agent_runner_module, "run", runner)

    async with Client(mcp) as client:
        # First call: should time out and release the lock.
        first = await client.call_tool(
            "create_task",
            {"prompt": "first — will timeout", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )
        assert not is_error(first), (
            f"first create_task should still return task_id even though the "
            f"agent timed out: {extract_meta(first)!r}"
        )
        first_id = extract_data(first)["task_id"]

        # Confirm first is failed:timeout.
        st1 = await client.call_tool(
            "get_task_status", {"task_id": first_id}, raise_on_error=False
        )
        st1_data = extract_data(st1)
        assert st1_data.get("status") == "failed"
        assert st1_data.get("error_reason") == "timeout"

        # Second call: must succeed immediately — proves the lock is released.
        second = await client.call_tool(
            "create_task",
            {"prompt": "second — should succeed", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )
        assert not is_error(second), (
            f"second create_task after timeout should succeed (lock should be "
            f"released) — got isError=true: meta={extract_meta(second)!r}"
        )
        second_id = extract_data(second)["task_id"]
        assert second_id != first_id

        st2 = await client.call_tool(
            "get_task_status", {"task_id": second_id}, raise_on_error=False
        )
        st2_data = extract_data(st2)
        assert st2_data.get("status") == "completed", (
            f"second task did not reach completed: {st2_data!r}"
        )
