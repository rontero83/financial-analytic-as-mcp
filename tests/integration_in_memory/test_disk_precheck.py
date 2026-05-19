"""End-to-end disk-precheck coverage (D-41 / D-42 / D-43 / SC2).

Seven scenarios enumerated in 03-02-PLAN.md Task 2 <behavior>:

1. DISK_FULL wire shape — ``CallToolResult { isError: true, _meta: {
   error_code: "DISK_FULL", free_mb, threshold_mb } }``; no task dir
   created on refusal.
2. DISK_FULL does NOT acquire the single-task lock — a second call
   straight after the first ALSO returns DISK_FULL (not BUSY), proving
   the precheck short-circuits before the lock is touched.
3. Above-threshold disk usage proceeds normally — mock agent runs,
   task lands in ``completed``.
4. Threshold override via FSMC_FREE_SPACE_MB — same disk usage refused
   at threshold=200 and succeeds at threshold=100.
5. The precheck does NOT gate ``list_skills`` / ``get_task_status`` /
   ``get_task_result`` (D-43, read-only tools).
6. Invalid FSMC_FREE_SPACE_MB causes ``sys.exit(5)`` at lifespan
   startup (driven directly via ``async with app_lifespan(mcp)`` per
   the established universal-indexing fatal-path pattern).
7. The ``disk_precheck_refused`` D-39 event lands on stderr through
   the global structlog pipeline configured by the conftest autouse
   fixture (carries ``free_mb`` + ``threshold_mb``).
"""
from __future__ import annotations

import asyncio
import json
import types
from pathlib import Path

import pytest
from fastmcp import Client

import io

from finance_skills_mcp import agent_runner as _agent_runner_module
from finance_skills_mcp.logging_config import (
    _reset_for_tests as _reset_logging_for_tests,
    configure_logging,
)
from finance_skills_mcp.server import app_lifespan, mcp
from tests._fixtures.mock_agent_runner import MockAgentRunner
from tests.integration_in_memory._indexing_helpers import prime_auth
from tests.integration_live._client_helpers import (
    extract_data,
    extract_meta,
    is_error,
)


pytestmark = [pytest.mark.in_memory, pytest.mark.anyio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_disk_usage(free_bytes: int):
    """Return a function suitable for monkeypatching ``shutil.disk_usage``.

    The returned callable ignores its path argument and returns a
    ``types.SimpleNamespace`` with ``total``, ``used``, and ``free``
    fields — duck-typed to match ``os._DiskUsage`` (shutil.disk_usage's
    return type) since the production code only reads ``.free``.
    """

    def _impl(path):  # noqa: ARG001 — path intentionally ignored
        total = 10**12  # 1 TB fake volume
        return types.SimpleNamespace(total=total, used=total - free_bytes, free=free_bytes)

    return _impl


def _prime_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prime the env for an in-memory test: auth sentinel + scan-root override."""
    prime_auth(monkeypatch)
    monkeypatch.setenv("FSMC_SKILL_ROOTS", "tests/fixtures/skills")


def _patch_disk_usage(monkeypatch: pytest.MonkeyPatch, free_mb: int) -> None:
    """Replace ``shutil.disk_usage`` everywhere the server module sees it.

    The server module does ``import shutil`` (module attribute) then
    calls ``shutil.disk_usage`` via attribute access wrapped in
    ``anyio.to_thread.run_sync``. Patching the global ``shutil.disk_usage``
    therefore reaches the actual call site.
    """
    free_bytes = free_mb * 1024 * 1024
    monkeypatch.setattr("shutil.disk_usage", _fake_disk_usage(free_bytes))


# ---------------------------------------------------------------------------
# Test 1 — DISK_FULL wire shape + no task dir created
# ---------------------------------------------------------------------------


async def test_disk_full_returns_isError_with_disk_full_meta(
    monkeypatch: pytest.MonkeyPatch,
):
    """50 MB free + default threshold 100 -> DISK_FULL with required _meta keys."""
    _prime_skills(monkeypatch)
    _patch_disk_usage(monkeypatch, free_mb=50)  # default threshold is 100
    monkeypatch.setattr(
        _agent_runner_module, "run", MockAgentRunner(canned_output="ok").run
    )

    repo_root = Path(__file__).resolve().parents[2]
    tasks_root = repo_root / "tasks"
    tasks_root.mkdir(exist_ok=True)
    pre_existing = {p.name for p in tasks_root.iterdir() if p.is_dir()}

    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_task",
            {"prompt": "should be refused", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )

    assert is_error(result), (
        f"expected DISK_FULL error result; got success: data={extract_data(result)!r}"
    )
    meta = extract_meta(result)
    assert meta.get("error_code") == "DISK_FULL", (
        f"expected error_code=DISK_FULL; got meta={meta!r}"
    )
    assert meta.get("free_mb") == 50, (
        f"expected free_mb=50 (monkeypatched); got meta={meta!r}"
    )
    assert meta.get("threshold_mb") == 100, (
        f"expected threshold_mb=100 (default); got meta={meta!r}"
    )

    # The precheck runs BEFORE TaskManager.create() — no task dir is created.
    post_existing = {p.name for p in tasks_root.iterdir() if p.is_dir()}
    assert post_existing == pre_existing, (
        f"DISK_FULL refusal must not create a task dir; "
        f"new dirs: {post_existing - pre_existing}"
    )


# ---------------------------------------------------------------------------
# Test 2 — DISK_FULL does NOT acquire the single-task lock
# ---------------------------------------------------------------------------


async def test_disk_full_does_not_acquire_lock(monkeypatch: pytest.MonkeyPatch):
    """Two back-to-back DISK_FULL refusals — neither acquires the lock.

    Load-bearing assertion for D-42: the precheck short-circuits BEFORE
    the single-task lock is touched. If the first refusal accidentally
    acquired and never released the lock, the second call would return
    BUSY instead of DISK_FULL.
    """
    _prime_skills(monkeypatch)
    _patch_disk_usage(monkeypatch, free_mb=10)  # well below default 100
    monkeypatch.setattr(
        _agent_runner_module, "run", MockAgentRunner(canned_output="ok").run
    )

    async with Client(mcp) as client:
        first = await client.call_tool(
            "create_task",
            {"prompt": "first", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )
        second = await client.call_tool(
            "create_task",
            {"prompt": "second", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )

    for label, result in (("first", first), ("second", second)):
        assert is_error(result), f"{label} call should be DISK_FULL error"
        meta = extract_meta(result)
        assert meta.get("error_code") == "DISK_FULL", (
            f"{label} call meta should carry error_code=DISK_FULL; got {meta!r}"
        )
        assert "inflight_task_id" not in meta, (
            f"{label} call meta should NOT contain BUSY's inflight_task_id; "
            f"got {meta!r} — proves the lock was never acquired"
        )


# ---------------------------------------------------------------------------
# Test 3 — Above-threshold proceeds normally
# ---------------------------------------------------------------------------


async def test_above_threshold_proceeds_normally(monkeypatch: pytest.MonkeyPatch):
    """200 MB free + default 100 threshold -> create_task completes normally."""
    _prime_skills(monkeypatch)
    _patch_disk_usage(monkeypatch, free_mb=200)  # well above default 100
    mock_runner = MockAgentRunner(canned_output="# OK\n")
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_task",
            {"prompt": "should succeed", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )
        assert not is_error(result), (
            f"create_task unexpectedly errored: meta={extract_meta(result)!r}"
        )
        data = extract_data(result)
        task_id = data.get("task_id")
        assert task_id, f"missing task_id in success response: {data!r}"

        # Poll until terminal — the mock returns immediately, so one poll
        # should suffice but we give it three attempts for slack.
        for _ in range(5):
            status_result = await client.call_tool(
                "get_task_status", {"task_id": task_id}
            )
            status_data = extract_data(status_result)
            if status_data.get("status") in ("completed", "failed"):
                break
            await asyncio.sleep(0.1)

        assert status_data.get("status") == "completed", (
            f"task did not reach completed; final status={status_data!r}"
        )


# ---------------------------------------------------------------------------
# Test 4 — Threshold override via FSMC_FREE_SPACE_MB
# ---------------------------------------------------------------------------


async def test_threshold_override_via_env_refuses(monkeypatch: pytest.MonkeyPatch):
    """150 MB free + threshold=200 -> DISK_FULL (override raises bar)."""
    _prime_skills(monkeypatch)
    monkeypatch.setenv("FSMC_FREE_SPACE_MB", "200")
    _patch_disk_usage(monkeypatch, free_mb=150)
    monkeypatch.setattr(
        _agent_runner_module, "run", MockAgentRunner(canned_output="ok").run
    )

    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_task",
            {"prompt": "refused at threshold=200", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )

    assert is_error(result), "expected DISK_FULL with threshold=200"
    meta = extract_meta(result)
    assert meta.get("error_code") == "DISK_FULL"
    assert meta.get("free_mb") == 150
    assert meta.get("threshold_mb") == 200


async def test_threshold_override_via_env_proceeds(monkeypatch: pytest.MonkeyPatch):
    """150 MB free + explicit threshold=100 -> succeeds (override lowers bar)."""
    _prime_skills(monkeypatch)
    monkeypatch.setenv("FSMC_FREE_SPACE_MB", "100")
    _patch_disk_usage(monkeypatch, free_mb=150)
    mock_runner = MockAgentRunner(canned_output="ok")
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    async with Client(mcp) as client:
        result = await client.call_tool(
            "create_task",
            {"prompt": "succeeds at threshold=100", "skills": ["fixture-skill-alpha"]},
            raise_on_error=False,
        )
    assert not is_error(result), (
        f"expected success at threshold=100 with free=150; "
        f"got meta={extract_meta(result)!r}"
    )


# ---------------------------------------------------------------------------
# Test 5 — D-43 read-only tools are NOT gated
# ---------------------------------------------------------------------------


async def test_list_skills_status_result_are_not_gated(
    monkeypatch: pytest.MonkeyPatch,
):
    """list_skills / get_task_status / get_task_result are read-only (D-43).

    With disk usage at 10 MB (far below default 100 MB threshold), these
    three tools must NOT return DISK_FULL — they should serve their normal
    responses (catalog dict, TASK_NOT_FOUND for a synthesized task_id).
    """
    _prime_skills(monkeypatch)
    _patch_disk_usage(monkeypatch, free_mb=10)  # far below default
    monkeypatch.setattr(
        _agent_runner_module, "run", MockAgentRunner(canned_output="ok").run
    )

    async with Client(mcp) as client:
        # list_skills must return the catalog
        ls = await client.call_tool("list_skills", {}, raise_on_error=False)
        assert not is_error(ls), (
            f"list_skills must not be gated; got error meta={extract_meta(ls)!r}"
        )
        ls_data = extract_data(ls)
        assert "skills" in ls_data, f"list_skills shape broken: {ls_data!r}"

        # get_task_status against a syntactically valid but unknown task_id
        fake_id = "20260520T120000-deadbeef"
        gs = await client.call_tool(
            "get_task_status", {"task_id": fake_id}, raise_on_error=False
        )
        gs_meta = extract_meta(gs)
        # Must NOT be DISK_FULL; the natural error is TASK_NOT_FOUND.
        assert gs_meta.get("error_code") != "DISK_FULL", (
            f"get_task_status should not gate on disk; got {gs_meta!r}"
        )

        # get_task_result likewise
        gr = await client.call_tool(
            "get_task_result", {"task_id": fake_id}, raise_on_error=False
        )
        gr_meta = extract_meta(gr)
        assert gr_meta.get("error_code") != "DISK_FULL", (
            f"get_task_result should not gate on disk; got {gr_meta!r}"
        )


# ---------------------------------------------------------------------------
# Test 6 — Invalid FSMC_FREE_SPACE_MB -> sys.exit(5) at lifespan
# ---------------------------------------------------------------------------


async def test_invalid_env_exits_with_5(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
):
    """FSMC_FREE_SPACE_MB=-1 at lifespan startup -> SystemExit(5) + stderr."""
    prime_auth(monkeypatch)
    monkeypatch.setenv("FSMC_FREE_SPACE_MB", "-1")

    with pytest.raises(SystemExit) as exc_info:
        async with app_lifespan(mcp) as _ctx:
            pass  # pragma: no cover — lifespan must exit before yielding

    assert exc_info.value.code == 5, (
        f"expected SystemExit(5) for invalid FSMC_FREE_SPACE_MB; "
        f"got code={exc_info.value.code!r}"
    )
    captured = capsys.readouterr()
    assert "FSMC_FREE_SPACE_MB" in captured.err, (
        f"stderr must name the env var; got: {captured.err!r}"
    )
    assert "refusing to start" in captured.err, (
        f"stderr must say 'refusing to start'; got: {captured.err!r}"
    )


# ---------------------------------------------------------------------------
# Test 7 — disk_precheck_refused D-39 event is logged
# ---------------------------------------------------------------------------


async def test_disk_precheck_refused_event_logged(monkeypatch: pytest.MonkeyPatch):
    """The D-39 disk_precheck_refused event flows through structlog with free/threshold.

    Test approach: reconfigure structlog to write to an injected
    ``StringIO`` (the same DI seam unit tests in ``test_logging_config.py``
    use), drive the refusal, then parse the captured stream for the
    canonical event line. We CANNOT use ``capsys``/``capfd`` here
    because structlog's session-scoped ``PrintLoggerFactory`` binds to
    the ``sys.stderr`` object that existed when the conftest autouse
    fixture first called ``configure_logging()`` — that object pre-dates
    pytest's per-test stderr swap, so the capture fixtures never see
    the writes (visible only in pytest's own "Captured stderr call"
    pane via its log-routing internals).

    The reconfigure path is restored at test teardown by re-invoking
    the conftest's session fixture path so subsequent tests inherit
    the same stderr-bound pipeline.
    """
    _prime_skills(monkeypatch)
    _patch_disk_usage(monkeypatch, free_mb=25)
    monkeypatch.setattr(
        _agent_runner_module, "run", MockAgentRunner(canned_output="ok").run
    )

    # Reconfigure structlog to a StringIO for the duration of this test.
    captured_stream = io.StringIO()
    _reset_logging_for_tests()
    configure_logging(stream=captured_stream)
    try:
        async with Client(mcp) as client:
            result = await client.call_tool(
                "create_task",
                {"prompt": "trigger event", "skills": ["fixture-skill-alpha"]},
                raise_on_error=False,
            )
        assert is_error(result), "precondition: call must be refused"
    finally:
        # Restore the session-wide configuration so subsequent tests
        # continue to share the conftest's session-bound stderr logger.
        _reset_logging_for_tests()
        configure_logging()  # rebinds to sys.stderr per default

    # Walk every captured line, try-parse as JSON, and assert at least one
    # is the disk_precheck_refused event carrying both numeric fields.
    matched: list[dict] = []
    for line in captured_stream.getvalue().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("event") == "disk_precheck_refused":
            matched.append(payload)

    assert matched, (
        f"expected at least one disk_precheck_refused JSON line; "
        f"captured stream was: {captured_stream.getvalue()!r}"
    )
    event = matched[0]
    assert event.get("free_mb") == 25, (
        f"event missing free_mb=25; got {event!r}"
    )
    assert event.get("threshold_mb") == 100, (
        f"event missing threshold_mb=100; got {event!r}"
    )
    # D-38 binding sanity — the event also inherits the contextvars bound
    # at refusal time. tool_name is the most reliable field to assert.
    assert event.get("tool_name") == "create_task", (
        f"event missing tool_name=create_task from D-38 binding; got {event!r}"
    )
