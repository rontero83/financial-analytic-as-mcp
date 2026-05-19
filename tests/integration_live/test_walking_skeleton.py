"""Walking Skeleton — REAL Claude Agent SDK smoke test.

This is the empirical proof of EXEC-02 (fresh ``query()`` per task) and
EXEC-03 (skill loading via ``plugins=[{"type":"local", ...}]``). Unlike the
in-memory companion test (``tests/integration_in_memory/test_walking_skeleton.py``)
this file does NOT substitute ``MockAgentRunner`` — the call hits
``claude_agent_sdk.query()`` which spawns the Claude CLI subprocess and burns
~one real API call.

Marked ``@pytest.mark.live``. The integration_live conftest auto-skips when
``ANTHROPIC_API_KEY`` is unset (so default `pytest` runs are unaffected).

Success criteria (ROADMAP.md Phase 1 SC-1):
    list_skills → fixture-skill-alpha entry
    create_task → returns task_id
    get_task_status → transitions to completed (no 'pending'/'running')
    get_task_result → output_markdown contains FIXTURE-ECHO:: sentinel
"""
from __future__ import annotations

import asyncio
import re

import pytest
from fastmcp import Client

from finance_skills_mcp.server import mcp
from tests.integration_live._client_helpers import (
    extract_data,
    extract_meta,
    is_error,
)

TASK_ID_RE_PATTERN = r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$"


@pytest.mark.live
@pytest.mark.anyio
async def test_walking_skeleton():
    """End-to-end (real SDK): list_skills → create_task → poll → get_result."""
    prompt = "echo test prompt"

    async with Client(mcp) as client:
        # 1. list_skills
        ls = await client.call_tool("list_skills", {})
        assert not is_error(ls), f"list_skills returned error: {ls!r}"
        ls_data = extract_data(ls)
        assert "skills" in ls_data, f"missing 'skills' key: {ls_data!r}"
        skill_ids = [s["id"] for s in ls_data["skills"]]
        assert "fixture-skill-alpha" in skill_ids, (
            f"fixture-skill-alpha missing from catalog: {skill_ids}"
        )

        # 2. create_task — REAL SDK call. v1 single-task synchronous: the call
        # returns only after the task is terminal. Allow up to 5 minutes wall
        # time for the inner asyncio.wait_for(600s) cap; the SDK should beat
        # that easily for a 1-shot fixture skill.
        ct = await client.call_tool(
            "create_task",
            {"prompt": prompt, "skills": ["fixture-skill-alpha"]},
        )
        assert not is_error(ct), (
            f"create_task returned error: is_error={is_error(ct)} "
            f"meta={extract_meta(ct)!r} data={extract_data(ct)!r}"
        )
        ct_data = extract_data(ct)
        task_id = ct_data.get("task_id") or extract_meta(ct).get("task_id")
        assert task_id, f"no task_id in create_task response: {ct!r}"
        assert re.match(TASK_ID_RE_PATTERN, task_id), (
            f"task_id {task_id!r} does not match {TASK_ID_RE_PATTERN}"
        )

        # 3. get_task_status — must be terminal (completed). Per v1 contract,
        # create_task only returns after termination, but poll a few times
        # for shape clarity / future-proofing.
        final_status = None
        for _ in range(10):
            st = await client.call_tool("get_task_status", {"task_id": task_id})
            assert not is_error(st), (
                f"get_task_status returned error: {extract_meta(st)!r}"
            )
            st_data = extract_data(st)
            status_val = st_data.get("status")
            assert status_val in {"working", "completed", "failed"}, (
                f"unexpected status: {status_val!r}"
            )
            assert status_val not in {"pending", "running"}, (
                f"observed forbidden status: {status_val!r}"
            )
            if status_val in {"completed", "failed"}:
                final_status = status_val
                break
            await asyncio.sleep(0.5)
        assert final_status == "completed", (
            f"task {task_id} ended in status {final_status!r} (expected 'completed'); "
            f"last status payload: {st_data!r}"
        )

        # 4. get_task_result — sentinels must appear in output_markdown.
        gr = await client.call_tool("get_task_result", {"task_id": task_id})
        assert not is_error(gr), (
            f"get_task_result returned error: {extract_meta(gr)!r}"
        )
        gr_data = extract_data(gr)
        output_md = gr_data.get("output_markdown", "")
        assert "FIXTURE-ECHO::" in output_md, (
            f"sentinel 'FIXTURE-ECHO::' not in output_markdown. "
            f"Got first 500 chars: {output_md[:500]!r}"
        )
        assert "::END-FIXTURE" in output_md, (
            f"sentinel '::END-FIXTURE' not in output_markdown. "
            f"Got first 500 chars: {output_md[:500]!r}"
        )
