"""BUSY semantics (MCP-05).

Two guarantees this file proves empirically against the in-memory FastMCP
server with a slow stand-in for the real Claude SDK:

1. **BUSY wire shape** — while one ``create_task`` is in flight, a second
   ``create_task`` returns ``CallToolResult { isError: true, _meta: {
   inflight_task_id, started_at } }`` per ``specs/create-task/spec.md``
   §Response (BUSY error). The first task continues uninterrupted and reaches
   ``completed``; no new task directory is created for the BUSY caller.

2. **BUSY schema validation** — the BUSY response is validated against the
   JSON Schema embedded in ``specs/create-task/spec.md`` §Schemas (Response —
   BUSY error). This proves the §A4 ``ErrorToolResult`` wire shape conforms
   to the contract, not just our own assertions.

Dedup note: the D-21 / EXEC-07 200 ms event-loop hygiene test that used to
live here has moved to ``tests/integration_in_memory/test_event_loop.py``
(its canonical home per 01-04-PLAN.md Task 1 and 01-03-SUMMARY.md Deferred
Items #1). The 1-line cross-reference comment near the bottom of this file
preserves discoverability.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import jsonschema
import pytest
from fastmcp import Client

from finance_skills_mcp import agent_runner as _agent_runner_module
from finance_skills_mcp.server import mcp
from tests.integration_live._client_helpers import (
    extract_data,
    extract_meta,
    is_error,
)

# D-03 task_id regex (matched against the value returned for the IN-FLIGHT task).
TASK_ID_RE_PATTERN = r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$"
ISO_DATETIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)?$"
)


# ---------------------------------------------------------------------------
# Spec-schema extraction (BUSY response JSON Schema lives in specs/create-task/spec.md)
# ---------------------------------------------------------------------------


SPEC_PATH = (
    Path(__file__).resolve().parents[2]
    / "specs"
    / "create-task"
    / "spec.md"
)


def _load_busy_response_schema() -> dict:
    """Parse the BUSY response JSON Schema out of specs/create-task/spec.md.

    The spec embeds schemas under ``### Response (BUSY error)`` followed by a
    fenced ```json``` block. We pluck that one block by anchor.
    """
    text = SPEC_PATH.read_text(encoding="utf-8")
    anchor = "### Response (BUSY error)"
    idx = text.index(anchor)
    fence_open = text.index("```json", idx) + len("```json")
    fence_close = text.index("```", fence_open)
    return json.loads(text[fence_open:fence_close].strip())


# ---------------------------------------------------------------------------
# Helpers — assemble the wire-equivalent payload from a FastMCP Client result
# ---------------------------------------------------------------------------


def _result_to_wire_dict(result) -> dict:
    """Reconstruct the on-the-wire CallToolResult dict from a Client result.

    FastMCP's in-memory client unpacks the ``CallToolResult`` into typed
    attributes; the JSON Schema in specs/create-task/spec.md targets the
    on-the-wire dict shape. We reassemble enough fields for schema validation:
    ``isError``, ``content``, ``_meta``.
    """
    wire: dict = {
        "isError": bool(getattr(result, "is_error", False)),
    }
    # content: list of blocks. FastMCP exposes typed objects; we coerce to
    # the minimal {type, text} dict the schema expects (the schema only
    # asserts content is an array, but we want it well-formed for clarity).
    content_attr = getattr(result, "content", None) or []
    wire_content = []
    for block in content_attr:
        if hasattr(block, "model_dump"):
            wire_content.append(block.model_dump(exclude_none=True))
        elif hasattr(block, "__dict__"):
            wire_content.append({k: v for k, v in vars(block).items() if v is not None})
        else:
            wire_content.append({"type": "text", "text": str(block)})
    wire["content"] = wire_content
    # _meta — straight passthrough from the server's ErrorToolResult.meta.
    meta = extract_meta(result)
    if meta:
        wire["_meta"] = dict(meta)
    return wire


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_busy_while_task_in_flight(monkeypatch):
    """A second create_task while the first is in flight returns BUSY.

    Task A starts with a slow mock runner (2 s sleep). Before A completes,
    Task B fires ``create_task`` and gets back the BUSY error shape with
    ``inflight_task_id`` == A's task_id. A then completes normally with
    status ``completed``.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-busy-test-key")
    # Phase 2 / D-23: route the indexer at the test-fixtures skill root so
    # ``fixture-skill-alpha`` appears in the catalog (D-34 retired the seed).
    monkeypatch.setenv("FSMC_SKILL_ROOTS", "tests/fixtures/skills")

    # Slow mock — sleeps long enough that Client B's call arrives during A.
    sleep_seconds = 2.0
    prompt_a = "task A prompt"
    prompt_b = "task B prompt — should BUSY"

    async def slow_runner(prompt, skills, cwd):
        await asyncio.sleep(sleep_seconds)
        return f"FIXTURE-ECHO::{prompt}::END-FIXTURE"

    monkeypatch.setattr(_agent_runner_module, "run", slow_runner)

    # Snapshot existing task dirs so we can assert the BUSY caller does not
    # create a new directory (spec.md Scenario: Second call returns BUSY).
    repo_root = Path(__file__).resolve().parents[2]
    tasks_root = repo_root / "tasks"
    tasks_root.mkdir(exist_ok=True)
    pre_existing = {
        p.name for p in tasks_root.iterdir() if p.is_dir()
    }

    async with Client(mcp) as client:
        # Fire Task A — DO NOT await yet.
        task_a = asyncio.create_task(
            client.call_tool(
                "create_task",
                {"prompt": prompt_a, "skills": ["fixture-skill-alpha"]},
            ),
            name="task-A",
        )

        # Let A get past validation + lock acquire + status.json write.
        # The slow_runner has not yet entered asyncio.sleep — the await above
        # only schedules the call; we hand the event loop ~300 ms.
        await asyncio.sleep(0.3)
        assert not task_a.done(), (
            "Task A finished before BUSY check could run — slow_runner too fast?"
        )

        # Fire Task B synchronously — must come back as BUSY.
        # raise_on_error=False keeps the ErrorToolResult shape intact instead
        # of having the FastMCP client convert isError=True into a ToolError.
        busy_result = await client.call_tool(
            "create_task",
            {"prompt": prompt_b, "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )

        assert is_error(busy_result), (
            f"Expected BUSY error but got success: data={extract_data(busy_result)!r}"
        )
        meta = extract_meta(busy_result)
        assert "inflight_task_id" in meta, (
            f"BUSY response missing _meta.inflight_task_id: meta={meta!r}"
        )
        assert "started_at" in meta, (
            f"BUSY response missing _meta.started_at: meta={meta!r}"
        )
        inflight = meta["inflight_task_id"]
        assert re.match(TASK_ID_RE_PATTERN, inflight), (
            f"inflight_task_id {inflight!r} does not match D-03 regex"
        )
        assert ISO_DATETIME_RE.match(meta["started_at"]), (
            f"started_at {meta['started_at']!r} is not an ISO timestamp"
        )
        # Per D-23, the BUSY shape carries inflight_task_id + started_at, NOT
        # an error_code field — that's the validation-error shape.
        assert "error_code" not in meta, (
            f"BUSY response should NOT carry error_code: {meta!r}"
        )

        # Now finish Task A — should complete cleanly.
        a_result = await task_a
        assert not is_error(a_result), (
            f"Task A unexpectedly errored: meta={extract_meta(a_result)!r}"
        )
        a_data = extract_data(a_result)
        task_a_id = a_data.get("task_id")
        assert task_a_id is not None
        # The BUSY response's inflight_task_id MUST match A's task_id.
        assert inflight == task_a_id, (
            f"BUSY inflight_task_id {inflight!r} != Task A's id {task_a_id!r}"
        )

        # Task A's status.json should be terminal=completed.
        a_status = await client.call_tool(
            "get_task_status", {"task_id": task_a_id}
        )
        assert not is_error(a_status)
        st = extract_data(a_status)
        assert st.get("status") == "completed", (
            f"Task A did not reach completed: {st!r}"
        )

        # Task B should NOT have created a new task directory.
        post_existing = {p.name for p in tasks_root.iterdir() if p.is_dir()}
        new_dirs = post_existing - pre_existing
        # Exactly one new dir — Task A's — should be present.
        assert new_dirs == {task_a_id}, (
            f"BUSY caller created an unexpected task dir: new_dirs={new_dirs!r}, "
            f"task_a_id={task_a_id!r}"
        )


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_busy_response_has_correct_shape(monkeypatch):
    """The BUSY result validates against the JSON Schema in specs/create-task/spec.md.

    Runtime validation of the §A4 ``ErrorToolResult`` wire shape against the
    contract — proves spec ↔ implementation parity (this is the integration
    counterpart to the static contract-test in ``tests/unit/test_contract.py``).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-busy-shape-test-key")
    # Phase 2 / D-23: route the indexer at the test-fixtures skill root.
    monkeypatch.setenv("FSMC_SKILL_ROOTS", "tests/fixtures/skills")

    async def slow_runner(prompt, skills, cwd):
        await asyncio.sleep(1.5)
        return "ok"

    monkeypatch.setattr(_agent_runner_module, "run", slow_runner)

    schema = _load_busy_response_schema()

    async with Client(mcp) as client:
        a = asyncio.create_task(
            client.call_tool(
                "create_task",
                {"prompt": "first", "skills": ["fixture-skill-alpha"]},
            ),
            name="task-A-shape",
        )
        await asyncio.sleep(0.3)
        try:
            busy = await client.call_tool(
                "create_task",
                {"prompt": "second", "skills": ["fixture-skill-alpha"]},
                raise_on_error=False,
            )
            wire = _result_to_wire_dict(busy)
            # `started_at` from the server uses ISO 8601 with timezone offset
            # (e.g. `+00:00`); the JSON Schema's `format: date-time` accepts
            # this. jsonschema treats format strictly only when a format
            # checker is supplied — we pass one so the test is honest.
            jsonschema.validate(
                wire,
                schema,
                format_checker=jsonschema.FormatChecker(),
            )
        finally:
            await a  # let Task A drain cleanly


# Canonical D-21 test lives in test_event_loop.py — moved per Phase 1 dedup
# (01-04-PLAN.md Task 1; 01-03-SUMMARY.md Deferred Items #1).
