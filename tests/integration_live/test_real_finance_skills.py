"""Nightly live-skill integration suite (Phase 3 SC3).

Three live-marked test cases that exercise ``create_task`` against the
three real finance skills shipped with the repo, using the REAL Claude Agent
SDK (no MockAgentRunner). Mirrors the wire pattern from
``tests/integration_live/test_walking_skeleton.py`` but with real skill IDs and
the per-skill prompts locked by D-45.

Real skills under test:
    - financial-analyst              (skills/financial-analyst/SKILL.md)
    - saas-metrics-coach             (skills/saas-metrics-coach/SKILL.md)
    - business-investment-advisor    (skills/business-investment-advisor/SKILL.md)

Success criteria:
    Nightly CI invokes ``create_task`` against the real finance skills via the
    live Claude Agent SDK and asserts non-empty ``output.md`` + ``completed``
    status, within the configured per-task timeout (default 600 s).

The conftest in this directory auto-skips these tests when neither
``ANTHROPIC_API_KEY`` nor ``CLAUDE_CODE_OAUTH_TOKEN`` is set, so default
developer ``pytest`` runs are unaffected. The nightly GitHub Actions workflow
(``.github/workflows/nightly-live.yml``, D-44/D-47) supplies the key from a
repository secret and runs this file via ``pytest -m live``.

Each test function is independent — no shared client or task_id fixture —
because the server is single-task synchronous (Phase 1 EXEC-01); parallel
execution would correctly hit BUSY which is not what we're proving here.
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

# Same shape as Phase 1 D-05: <YYYYMMDDThhmmss>-<8hex>.
TASK_ID_RE_PATTERN = r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$"


async def _run_live_skill(client: Client, skill_id: str, prompt: str) -> None:
    """Shared body: list_skills → create_task → poll status → get_task_result.

    Assertions:
      * ``skill_id`` appears in ``list_skills`` catalog.
      * ``create_task`` returns a well-formed ``task_id``.
      * Final status is ``completed`` (never the forbidden ``pending``/
        ``running`` tokens, never ``failed``).
      * ``output_markdown`` is a non-empty string (SC3 literal: "non-empty
        ``output.md``"). The content itself is NOT asserted — the skills are
        real and the model may phrase responses freely; this is a wire-up
        proof, not a behavior verifier (per D-46 + PITFALLS.md Pitfall 13).
    """
    # 1. list_skills — defensive sanity check that the indexer sees this skill.
    ls = await client.call_tool("list_skills", {})
    assert not is_error(ls), f"list_skills returned error: {ls!r}"
    ls_data = extract_data(ls)
    assert "skills" in ls_data, f"missing 'skills' key: {ls_data!r}"
    skill_ids = [s["id"] for s in ls_data["skills"]]
    assert skill_id in skill_ids, (
        f"{skill_id!r} missing from indexed catalog. "
        f"Got skill_ids={skill_ids!r}"
    )

    # 2. create_task — REAL SDK call. v1 single-task synchronous: returns only
    # after the task is terminal. Effective wall-time ceiling is the server's
    # TASK_TIMEOUT_SECONDS (EXEC-06 default 600 s); no client-side timeout.
    ct = await client.call_tool(
        "create_task",
        {"prompt": prompt, "skills": [skill_id]},
    )
    assert not is_error(ct), (
        f"create_task({skill_id!r}) returned error: is_error={is_error(ct)} "
        f"meta={extract_meta(ct)!r} data={extract_data(ct)!r}"
    )
    ct_data = extract_data(ct)
    task_id = ct_data.get("task_id") or extract_meta(ct).get("task_id")
    assert task_id, f"no task_id in create_task response: {ct!r}"
    assert re.match(TASK_ID_RE_PATTERN, task_id), (
        f"task_id {task_id!r} does not match {TASK_ID_RE_PATTERN}"
    )

    # 3. get_task_status — must be terminal (completed). Per v1 contract,
    # create_task only returns after termination, but poll a few times for
    # shape clarity and parity with test_walking_skeleton.py.
    final_status = None
    st_data: dict = {}
    for _ in range(10):
        st = await client.call_tool("get_task_status", {"task_id": task_id})
        assert not is_error(st), (
            f"get_task_status({task_id!r}) returned error: {extract_meta(st)!r}"
        )
        st_data = extract_data(st)
        status_val = st_data.get("status")
        assert status_val in {"working", "completed", "failed"}, (
            f"unexpected status: {status_val!r}"
        )
        assert status_val not in {"pending", "running"}, (
            f"observed forbidden status token: {status_val!r}"
        )
        if status_val in {"completed", "failed"}:
            final_status = status_val
            break
        await asyncio.sleep(0.5)
    assert final_status == "completed", (
        f"task {task_id} ({skill_id}) ended in status {final_status!r} "
        f"(expected 'completed'); last status payload: {st_data!r}"
    )

    # 4. get_task_result — SC3 literal: non-empty output_markdown.
    gr = await client.call_tool("get_task_result", {"task_id": task_id})
    assert not is_error(gr), (
        f"get_task_result({task_id!r}) returned error: {extract_meta(gr)!r}"
    )
    gr_data = extract_data(gr)
    output_md = gr_data.get("output_markdown")
    assert isinstance(output_md, str), (
        f"output_markdown is not a string: type={type(output_md).__name__} "
        f"value={output_md!r}"
    )
    assert len(output_md.strip()) > 0, (
        f"output_markdown is empty for {skill_id!r} task {task_id!r}. "
        f"Full result: {gr_data!r}"
    )


@pytest.mark.live
@pytest.mark.anyio
async def test_real_financial_analyst():
    """Real financial-analyst skill, 1-sentence service-summary prompt (D-45)."""
    prompt = "In one sentence, what financial analysis services can you provide?"
    async with Client(mcp) as client:
        await _run_live_skill(client, "financial-analyst", prompt)


@pytest.mark.live
@pytest.mark.anyio
async def test_real_saas_metrics_coach():
    """Real saas-metrics-coach skill, 1-sentence specialization prompt (D-45)."""
    prompt = "In one sentence, what SaaS metrics do you specialize in?"
    async with Client(mcp) as client:
        await _run_live_skill(client, "saas-metrics-coach", prompt)


@pytest.mark.live
@pytest.mark.anyio
async def test_real_business_investment_advisor():
    """Real business-investment-advisor skill, 1-sentence guidance prompt (D-45)."""
    prompt = "In one sentence, what investment guidance do you offer?"
    async with Client(mcp) as client:
        await _run_live_skill(client, "business-investment-advisor", prompt)
