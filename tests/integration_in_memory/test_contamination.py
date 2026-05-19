"""OPS-04 / D-19 — two-task contamination test (in-memory MockAgentRunner).

Proves EXEC-02: a fresh ``query()`` per task. The Claude Agent SDK is *not*
resumed across tasks; therefore information given only to Task 1 MUST NOT
appear in Task 2's output, even though both tasks execute in the same
process against the same singleton ``TaskManager`` / catalog / lock manager.

How the test simulates EXEC-02:
- Substitute ``finance_skills_mcp.agent_runner.run`` with an ``echo_runner``
  coroutine that returns ``FIXTURE-ECHO::<prompt>::END-FIXTURE``.
- Task 1's prompt embeds a unique magic value (``42-xyz-zyx``) — the
  ``echo_runner`` will echo it.
- Task 2's prompt asks "what was the magic number?" — the ``echo_runner``
  has no per-task memory (it's a stateless coroutine), so its output
  contains Task 2's prompt verbatim, NOT Task 1's magic value.
- The assertion locks in: Task 2's ``output_markdown`` must NOT contain the
  unique magic value from Task 1.

If a future refactor accidentally reuses the SDK session across tasks
(e.g. by adding ``resume=`` or ``continue_conversation=True``), this echo
test would still pass because the mock has no session state. The
*production* EXEC-02 guarantee is enforced by ``agent_runner.run`` not
passing those flags (verified in 01-01-SUMMARY.md key-decisions). The role
of this test is to prove that the wire path between MCP tools and the
runner does not leak state in any other way (shared TaskManager-level
caches, accidental prompt concatenation, etc.).

Reference: 01-04-PLAN.md Task 1; 01-RESEARCH.md §Contamination Test (D-19);
01-CONTEXT.md D-19.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp import Client

from finance_skills_mcp import agent_runner as _agent_runner_module
from finance_skills_mcp.server import mcp

# Verified §A5 attribute names — reuse the canonical helpers from 01-02 so we
# do NOT redefine them inline (per executor critical-constraints).
from tests.integration_live._client_helpers import (
    extract_data,
    extract_meta,
    is_error,
)

# D-03 task_id regex.
TASK_ID_RE_PATTERN = r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$"

# A magic value embedded ONLY in Task 1's prompt. If it appears in Task 2's
# output, the contamination contract is violated.
MAGIC_VALUE = "42-xyz-zyx"


async def echo_runner(prompt: str, skills, cwd: Path) -> str:
    """Stateless echo — mirrors fixture-skill-alpha's D-16 response contract.

    Returning the prompt verbatim is essential: it proves Task 2 saw Task
    2's prompt (not Task 1's). If TaskManager ever accidentally fed Task
    1's prompt into Task 2's runner call, the magic value would show up
    here.
    """
    return f"FIXTURE-ECHO::{prompt}::END-FIXTURE"


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_two_task_contamination(monkeypatch):
    """Task 2 output MUST NOT contain Task 1's magic value (OPS-04 / EXEC-02)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-contamination-test-key")
    # Phase 2 / D-23: route the indexer at the test-fixtures skill root so
    # ``fixture-skill-alpha`` appears in the catalog (D-34 retired the seed).
    monkeypatch.setenv("FSMC_SKILL_ROOTS", "tests/fixtures/skills")

    # Patch the module-level `run` attribute. TaskManager stores a reference
    # to the agent_runner module (per 01-01 DI seam) and calls
    # `self.agent_runner.run(...)` — patching the module attribute swaps in
    # echo_runner without touching the production code.
    with patch.object(_agent_runner_module, "run", new=echo_runner):
        async with Client(mcp) as client:
            # --- Task 1: feed the magic value ---
            prompt_1 = f"remember the magic number is {MAGIC_VALUE}"
            ct1 = await client.call_tool(
                "create_task",
                {"prompt": prompt_1, "skills": ["fixture-skill-alpha"]},
                raise_on_error=False,  # per 01-03 finding — preserve _meta
            )
            assert not is_error(ct1), (
                f"Task 1 create_task errored: meta={extract_meta(ct1)!r}"
            )
            # verified per 01-01-SUMMARY.md §A5: ``result.data`` carries the
            # deserialized return dict from the tool handler.
            ct1_data = extract_data(ct1)
            task_id_1 = ct1_data.get("task_id") or extract_meta(ct1).get("task_id")
            assert task_id_1, f"Task 1: no task_id in response: {ct1!r}"
            assert re.match(TASK_ID_RE_PATTERN, task_id_1), (
                f"Task 1 id {task_id_1!r} does not match D-03 regex"
            )

            # Poll Task 1 to completion (mock returns synchronously, but be
            # explicit about reaching a terminal state before firing Task 2).
            final_1 = None
            for _ in range(50):
                st = await client.call_tool(
                    "get_task_status",
                    {"task_id": task_id_1},
                    raise_on_error=False,
                )
                assert not is_error(st), (
                    f"get_task_status (Task 1) errored: {extract_meta(st)!r}"
                )
                # verified per 01-01-SUMMARY.md §A5: ``result.data`` carries
                # the status payload dict (`status`, `started_at`, ...).
                st_data = extract_data(st)
                status_val = st_data.get("status")
                assert status_val in {"working", "completed", "failed"}, (
                    f"unexpected status {status_val!r}"
                )
                if status_val in {"completed", "failed"}:
                    final_1 = status_val
                    break
                await asyncio.sleep(0.05)
            assert final_1 == "completed", (
                f"Task 1 did not complete: final_status={final_1!r}"
            )

            # Sanity: Task 1's output contains the magic value (it MUST — the
            # echo runner echoed Task 1's prompt). If this fails, the test
            # itself is broken before we can even check Task 2.
            gr1 = await client.call_tool(
                "get_task_result",
                {"task_id": task_id_1},
                raise_on_error=False,
            )
            assert not is_error(gr1), (
                f"get_task_result (Task 1) errored: {extract_meta(gr1)!r}"
            )
            # verified per 01-01-SUMMARY.md §A5: ``result.data`` carries
            # ``{"output_markdown": ..., "metadata": ...}``.
            output_1 = extract_data(gr1).get("output_markdown", "")
            assert MAGIC_VALUE in output_1, (
                f"Task 1 echo did not contain magic value — test setup broken. "
                f"output_1={output_1!r}"
            )

            # --- Task 2: ask about the magic value (must NOT see it) ---
            prompt_2 = "what was the magic number?"
            ct2 = await client.call_tool(
                "create_task",
                {"prompt": prompt_2, "skills": ["fixture-skill-alpha"]},
                raise_on_error=False,
            )
            assert not is_error(ct2), (
                f"Task 2 create_task errored: meta={extract_meta(ct2)!r}"
            )
            ct2_data = extract_data(ct2)
            task_id_2 = ct2_data.get("task_id") or extract_meta(ct2).get("task_id")
            assert task_id_2, f"Task 2: no task_id in response: {ct2!r}"
            assert task_id_2 != task_id_1, (
                "Task 2 got the SAME task_id as Task 1 — id generation broken"
            )

            # Poll Task 2 to completion.
            final_2 = None
            for _ in range(50):
                st = await client.call_tool(
                    "get_task_status",
                    {"task_id": task_id_2},
                    raise_on_error=False,
                )
                assert not is_error(st)
                st_data = extract_data(st)
                status_val = st_data.get("status")
                if status_val in {"completed", "failed"}:
                    final_2 = status_val
                    break
                await asyncio.sleep(0.05)
            assert final_2 == "completed", (
                f"Task 2 did not complete: final_status={final_2!r}"
            )

            # The load-bearing assertion: Task 2's output must NOT contain the
            # magic value from Task 1. The echo runner is stateless, so the
            # only way it would leak is if TaskManager / agent_runner / the
            # MCP plumbing accidentally cross-fed prompts between tasks.
            gr2 = await client.call_tool(
                "get_task_result",
                {"task_id": task_id_2},
                raise_on_error=False,
            )
            assert not is_error(gr2), (
                f"get_task_result (Task 2) errored: {extract_meta(gr2)!r}"
            )
            output_2 = extract_data(gr2).get("output_markdown", "")
            assert MAGIC_VALUE not in output_2, (
                f"CONTAMINATION: Task 2 output contains magic value "
                f"{MAGIC_VALUE!r} from Task 1: output_2={output_2!r}"
            )
            # Positive assertion: Task 2's echo really did echo Task 2's
            # prompt (proves the echo runner ran for the right prompt).
            assert prompt_2 in output_2, (
                f"Task 2 echo missing Task 2's prompt: output_2={output_2!r}"
            )
