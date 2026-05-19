"""Walking Skeleton — in-memory MCP smoke test with MockAgentRunner.

Mirrors ``tests/integration_live/test_walking_skeleton.py`` but substitutes the
real ``claude_agent_sdk.query()`` for a deterministic ``MockAgentRunner`` so the
test runs in <1 second with zero API spend. This proves the FastMCP wire path
(Client → server.py → TaskManager → task_store → status.json / output.md) end
to end; the live test (separate file) is the empirical proof of EXEC-02/03 with
the real SDK.

Reference: 01-02-PLAN.md (executor context expands the original "live-only"
plan to include this in-memory tier — same flow, mocked agent).
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client

from finance_skills_mcp import agent_runner as _agent_runner_module
from finance_skills_mcp.server import mcp
from tests._fixtures.mock_agent_runner import MockAgentRunner
from tests.integration_live._client_helpers import (
    extract_data,
    extract_meta,
    is_error,
)


# Regex pattern matching the canonical task_id format (D-03):
# YYYYMMDDTHHMMSS-<8 hex chars>.
TASK_ID_RE_PATTERN = r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$"


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_walking_skeleton_in_memory(monkeypatch):
    """End-to-end (mocked agent): list_skills → create_task → poll → get_result.

    Substitutes ``agent_runner.run`` with ``MockAgentRunner.run`` so the
    create_task call completes deterministically with a canned echo body.
    """
    import re

    # The auth smoke test (D-12) in server.app_lifespan checks for ANTHROPIC_API_KEY
    # and sys.exit(2)s if absent — that would kill the test process. We don't need
    # a real key for in-memory tests (MockAgentRunner bypasses the SDK entirely),
    # so set a sentinel value just to satisfy the env-var-existence check.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-in-memory-test-key")
    # Phase 2 / D-23: route the indexer at the test-fixtures skill root so
    # ``fixture-skill-alpha`` appears in the catalog. After D-34 retired the
    # in-``src/`` seed, this env-var is the canonical Phase-1-style override.
    monkeypatch.setenv("FSMC_SKILL_ROOTS", "tests/fixtures/skills")

    # Deterministic mock — echoes the prompt exactly like fixture-skill-alpha
    # tells the live agent to do (FIXTURE-ECHO::<prompt>::END-FIXTURE).
    prompt = "echo test prompt"
    mock_runner = MockAgentRunner(
        canned_output=f"FIXTURE-ECHO::{prompt}::END-FIXTURE"
    )
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    async with Client(mcp) as client:
        # 1. list_skills — fixture-skill-alpha must be present.
        ls = await client.call_tool("list_skills", {})
        assert not is_error(ls), f"list_skills returned error: {ls!r}"
        ls_data = extract_data(ls)
        assert "skills" in ls_data, f"missing 'skills' key: {ls_data!r}"
        skill_ids = [s["id"] for s in ls_data["skills"]]
        assert "fixture-skill-alpha" in skill_ids, (
            f"fixture-skill-alpha missing from catalog: {skill_ids}"
        )

        # list_skills tool annotation: readOnlyHint=True (verified at registration).
        # We can't introspect ToolAnnotations through the in-memory Client API
        # cleanly, but the server.py registration call sets it; we assert via
        # list_tools().
        tools = await client.list_tools()
        ls_tool = next((t for t in tools if t.name == "list_skills"), None)
        assert ls_tool is not None, "list_skills tool must be registered"
        # Tool annotations live on `Tool.annotations` in FastMCP. The shape is
        # mcp.types.ToolAnnotations(readOnlyHint=True, ...).
        if ls_tool.annotations is not None:
            assert getattr(ls_tool.annotations, "readOnlyHint", None) is True, (
                f"list_skills.readOnlyHint must be True, got annotations={ls_tool.annotations!r}"
            )

        # 2. create_task — should succeed with mock; task_id in response body.
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

        # 3. get_task_status — must transition to completed (mock runs ~instantly,
        # but create_task is synchronous in v1: when call_tool returns, the
        # task is already terminal). Poll once for shape clarity.
        final_status = None
        for _ in range(20):  # generous; mock terminates synchronously
            st = await client.call_tool("get_task_status", {"task_id": task_id})
            assert not is_error(st), (
                f"get_task_status returned error: {extract_meta(st)!r}"
            )
            st_data = extract_data(st)
            assert "status" in st_data, f"status.json missing 'status': {st_data!r}"
            status_val = st_data["status"]
            assert status_val in {"working", "completed", "failed"}, (
                f"unexpected status: {status_val!r}"
            )
            # Per the v1 single-task synchronous contract, create_task only
            # returns AFTER the task is terminal — we should never observe
            # 'pending' or 'running' (those are not part of the status enum).
            assert status_val not in {"pending", "running"}, (
                f"observed forbidden status: {status_val!r}"
            )
            if status_val in {"completed", "failed"}:
                final_status = status_val
                break
            await asyncio.sleep(0.05)
        assert final_status == "completed", (
            f"task ended in status {final_status!r} (expected 'completed')"
        )

        # 4. get_task_result — output_markdown carries the sentinel.
        gr = await client.call_tool("get_task_result", {"task_id": task_id})
        assert not is_error(gr), (
            f"get_task_result returned error: {extract_meta(gr)!r}"
        )
        gr_data = extract_data(gr)
        output_md = gr_data.get("output_markdown", "")
        assert "FIXTURE-ECHO::" in output_md, (
            f"sentinel 'FIXTURE-ECHO::' not in output_markdown: {output_md[:200]!r}"
        )
        assert "::END-FIXTURE" in output_md, (
            f"sentinel '::END-FIXTURE' not in output_markdown: {output_md[:200]!r}"
        )
        # The mock echoes the exact prompt — assert the verbatim shape.
        assert output_md == f"FIXTURE-ECHO::{prompt}::END-FIXTURE", (
            f"mock did not echo verbatim: {output_md!r}"
        )

        # Sanity: the mock was actually invoked (proves the DI seam works).
        assert len(mock_runner.calls) == 1, (
            f"MockAgentRunner expected 1 call; got {len(mock_runner.calls)}"
        )
        called_prompt, called_skills, _called_cwd = mock_runner.calls[0]
        assert called_prompt == prompt
        assert called_skills == ("fixture-skill-alpha",)
