"""Per-task logging integration tests (Phase 3 / OPS-01 / SC1 / D-37..D-39).

Drives ``create_task`` end-to-end via the in-memory FastMCP ``Client(mcp)``
with ``MockAgentRunner`` substituted, then reads the per-task
``tasks/<task_id>/logs/server.jsonl`` file and asserts the wire shape:

- Test 1: file exists; every line is parseable JSON.
- Test 2: every line carries the three D-38 fields + ``event``.
- Test 3: the D-39 minimum event set is a subset of the observed events.
- Test 4: ``agent_call_returned`` carries an integer ``elapsed_ms``.
- Test 5: a failed task emits ``task_failed`` with ``error_class``.
- Test 6: status polls during a logged task stay < 200 ms (D-21 / EXEC-07
  regression check with per-task file writes active).
- Test 7: log-injection guard — a prompt containing a forged JSON payload
  must not splice into the log structure (T-03-01-01 mitigation).

Reference: 03-01-PLAN.md Task 2 <behavior>.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

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


REPO_ROOT = Path(__file__).resolve().parents[2]


def _log_path_for(task_id: str) -> Path:
    """Return the canonical per-task log file path under the real repo root.

    The production ``app_lifespan`` computes ``tasks_root = repo_root /
    "tasks"`` from ``Path(__file__).resolve().parents[2]``, which is the
    actual project root — see 02-04 M-4 fix notes. Tests therefore read
    the file from that same on-disk location.
    """
    return REPO_ROOT / "tasks" / task_id / "logs" / "server.jsonl"


def _read_log_lines(task_id: str) -> list[dict]:
    """Read the per-task JSONL file and return a list of parsed dicts.

    Asserts each line parses as JSON; the caller can then do
    higher-level structural assertions.
    """
    log_path = _log_path_for(task_id)
    assert log_path.is_file(), f"per-task log file missing: {log_path}"
    text = log_path.read_text(encoding="utf-8")
    assert text, f"per-task log file is empty: {log_path}"
    lines = [line for line in text.splitlines() if line.strip()]
    parsed: list[dict] = []
    for i, line in enumerate(lines):
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"line {i} of {log_path} is not valid JSON: {line!r} ({exc})"
            )
    return parsed


async def _drive_create_task(
    client: Client,
    prompt: str,
    skills: list[str],
) -> tuple[bool, str | None, dict, dict]:
    """Helper: call create_task and return (is_err, task_id, data, meta)."""
    ct = await client.call_tool(
        "create_task",
        {"prompt": prompt, "skills": skills},
        raise_on_error=False,
    )
    err = is_error(ct)
    data = extract_data(ct)
    meta = extract_meta(ct)
    task_id = data.get("task_id") or meta.get("task_id")
    return err, task_id, data, meta


@pytest.fixture
def _set_env(monkeypatch):
    """Shared env-var setup mirroring test_walking_skeleton_in_memory."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-per-task-logging-test-key")
    monkeypatch.setenv("FSMC_SKILL_ROOTS", "tests/fixtures/skills")


# ---------------------------------------------------------------------------
# Test 1 — file exists, every line is JSON
# ---------------------------------------------------------------------------


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_log_file_exists_and_is_jsonl_after_completed_task(_set_env, monkeypatch):
    mock_runner = MockAgentRunner(canned_output="# Mock output\n")
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    async with Client(mcp) as client:
        err, task_id, _, _ = await _drive_create_task(
            client, "log file shape probe", ["fixture-skill-alpha"]
        )
        assert not err, "create_task unexpectedly errored"
        assert task_id

        lines = _read_log_lines(task_id)
        assert lines, "expected at least one log line"
        # Every entry has a string ``event`` field.
        for i, line in enumerate(lines):
            assert isinstance(line.get("event"), str), (
                f"line {i} missing string event: {line!r}"
            )


# ---------------------------------------------------------------------------
# Test 2 — D-38 fields on every line
# ---------------------------------------------------------------------------


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_required_d38_fields_on_every_line(_set_env, monkeypatch):
    mock_runner = MockAgentRunner(canned_output="ok\n")
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    skills_passed = ["fixture-skill-alpha"]

    async with Client(mcp) as client:
        err, task_id, _, _ = await _drive_create_task(
            client, "d38 probe", skills_passed
        )
        assert not err
        lines = _read_log_lines(task_id)

        for i, line in enumerate(lines):
            assert line.get("task_id") == task_id, (
                f"line {i} task_id mismatch: {line!r}"
            )
            assert line.get("tool_name") == "create_task", (
                f"line {i} tool_name wrong: {line!r}"
            )
            assert line.get("skill_ids") == skills_passed, (
                f"line {i} skill_ids wrong: {line!r}"
            )
            # snake_case event
            ev = line.get("event")
            assert isinstance(ev, str) and ev == ev.lower(), (
                f"line {i} event is not snake_case lowercase: {ev!r}"
            )


# ---------------------------------------------------------------------------
# Test 3 — D-39 minimum event set present (HIGH M-1 from plan-check)
# ---------------------------------------------------------------------------


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_minimum_d39_event_set_present(_set_env, monkeypatch):
    mock_runner = MockAgentRunner(canned_output="ok\n")
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    async with Client(mcp) as client:
        err, task_id, _, _ = await _drive_create_task(
            client, "d39 probe", ["fixture-skill-alpha"]
        )
        assert not err
        lines = _read_log_lines(task_id)

        events = {line["event"] for line in lines}
        required = {
            "task_started",
            "task_lock_acquired",
            "task_lock_released",
            "agent_call_started",
            "agent_call_returned",
            "task_completed",
        }
        missing = required - events
        assert not missing, (
            f"D-39 minimum event set incomplete: missing {sorted(missing)}; "
            f"observed events = {sorted(events)}"
        )


# ---------------------------------------------------------------------------
# Test 4 — agent_call_returned elapsed_ms
# ---------------------------------------------------------------------------


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_agent_call_returned_carries_elapsed_ms(_set_env, monkeypatch):
    mock_runner = MockAgentRunner(canned_output="ok\n")
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    async with Client(mcp) as client:
        err, task_id, _, _ = await _drive_create_task(
            client, "elapsed probe", ["fixture-skill-alpha"]
        )
        assert not err
        lines = _read_log_lines(task_id)
        ret = [line for line in lines if line["event"] == "agent_call_returned"]
        assert len(ret) == 1, (
            f"expected exactly 1 agent_call_returned line, got {len(ret)}"
        )
        elapsed = ret[0].get("elapsed_ms")
        assert isinstance(elapsed, int) and elapsed >= 0, (
            f"agent_call_returned.elapsed_ms wrong type/value: {ret[0]!r}"
        )


# ---------------------------------------------------------------------------
# Test 5 — task_failed with error_class
# ---------------------------------------------------------------------------


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_failed_task_emits_task_failed_with_error_class(_set_env, monkeypatch):
    mock_runner = MockAgentRunner(
        raise_on_run=RuntimeError("simulated SDK failure")
    )
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    async with Client(mcp) as client:
        err, task_id, _, _ = await _drive_create_task(
            client, "failure probe", ["fixture-skill-alpha"]
        )
        assert not err, (
            "create_task itself should not error — the failure surfaces "
            "inside the task lifecycle (terminal status=failed)."
        )
        lines = _read_log_lines(task_id)
        failed = [line for line in lines if line["event"] == "task_failed"]
        assert len(failed) == 1, (
            f"expected exactly 1 task_failed line, got {len(failed)} in {lines!r}"
        )
        f = failed[0]
        assert f.get("status") == "failed", f"status wrong: {f!r}"
        # MockAgentRunner wraps the raise in MockAgentRunnerError; the
        # error_class field names that exception type (or any subclass thereof).
        ec = f.get("error_class")
        assert isinstance(ec, str) and ec, (
            f"task_failed.error_class missing or empty: {f!r}"
        )


# ---------------------------------------------------------------------------
# Test 6 — D-21 / EXEC-07 invariant with logging active
# ---------------------------------------------------------------------------


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_status_polls_under_200ms_with_logging_enabled(_set_env, monkeypatch):
    """Status polls during a logged 2-second task stay under 200 ms each.

    Regression check that the per-task file open + line-buffered writes
    introduced in Plan 03-01 do NOT block the event loop. The polling
    storm pattern mirrors ``test_event_loop.py`` — kept as a sibling
    here so a future logging regression points at this plan.

    Per L-1 mitigation in 03-PLAN-CHECK.md: the per-poll budget is set
    to 250 ms (vs the 200 ms gate in test_event_loop.py) to absorb
    CI-runner tolerance variance specifically when the structured-log
    write path is in the hot loop.
    """
    async def slow_runner(prompt, skills, cwd):
        await asyncio.sleep(2.0)
        return "done"

    monkeypatch.setattr(_agent_runner_module, "run", slow_runner)

    async with Client(mcp) as client:
        running = asyncio.create_task(
            client.call_tool(
                "create_task",
                {"prompt": "long task w/ logging", "skills": ["fixture-skill-alpha"]},
            ),
            name="long-task-logging",
        )

        # Let the lock acquire + initial status.json + per-task log file open.
        await asyncio.sleep(0.3)
        assert not running.done(), (
            "slow_runner finished too fast — cannot exercise the in-flight window."
        )

        # Harvest in-flight task_id via BUSY probe.
        busy = await client.call_tool(
            "create_task",
            {"prompt": "probe", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )
        assert is_error(busy), (
            f"expected BUSY while long task in flight; got "
            f"data={extract_data(busy)!r}"
        )
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
                f"get_task_status errored during long task: {extract_meta(r)!r}"
            )
            return elapsed

        # 10 polls per plan spec; gathered concurrently to maximise loop pressure.
        durations = await asyncio.gather(*(timed_poll() for _ in range(10)))

        # Per L-1: 250 ms ceiling absorbs CI-runner tolerance for the
        # logging-active variant.
        slow = [(i, d) for i, d in enumerate(durations) if d >= 0.250]
        assert not slow, (
            f"Status polls exceeded 250 ms with logging enabled "
            f"(D-21 / EXEC-07 regression suspected):\n"
            + "\n".join(f"  poll[{i}] = {d * 1000:.1f} ms" for i, d in slow)
            + f"\nAll durations (ms): "
            + repr([round(d * 1000, 1) for d in durations])
        )

        a_result = await running
        assert not is_error(a_result), (
            f"long task errored unexpectedly: {extract_meta(a_result)!r}"
        )


# ---------------------------------------------------------------------------
# Test 7 — log-injection guard (T-03-01-01)
# ---------------------------------------------------------------------------


@pytest.mark.in_memory
@pytest.mark.anyio
async def test_prompt_is_escaped_not_interpolated_in_log_line(_set_env, monkeypatch):
    """User-controlled prompt MUST NOT splice into the JSON log structure.

    Mitigates T-03-01-01 (Tampering / log line shape): a prompt that
    embeds a forged ``{"event": "fake"}`` substring must not produce a
    log line whose ``event`` field equals ``"fake"``. structlog's
    JSONRenderer escapes string values via ``json.dumps`` — this test
    is the empirical guard.
    """
    mock_runner = MockAgentRunner(canned_output="ok\n")
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    forged = 'first line\nsecond line with " quote and {"event": "fake"} payload'

    async with Client(mcp) as client:
        err, task_id, _, _ = await _drive_create_task(
            client, forged, ["fixture-skill-alpha"]
        )
        assert not err
        lines = _read_log_lines(task_id)
        # Plan 03-01 explicitly forbids logging the prompt body, so the
        # forged payload should not appear at all in the log lines — the
        # ``event`` field is always a literal token, never user input.
        for i, line in enumerate(lines):
            ev = line.get("event")
            assert ev != "fake", (
                f"line {i} has event=='fake' — log injection succeeded: {line!r}"
            )
        # Verify task_started line is well-formed JSON (already proven by
        # _read_log_lines successfully parsing it, but make the
        # assertion explicit for documentation).
        started = [line for line in lines if line["event"] == "task_started"]
        assert started, "task_started missing despite successful completion"
