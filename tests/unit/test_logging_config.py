"""Unit tests for ``finance_skills_mcp.logging_config`` (Phase 3 / OPS-01).

Covers the five behaviours specified in 03-01-PLAN.md Task 1:

1. ``configure_logging`` installs the JSONRenderer (output is JSON).
2. Default level is INFO (DEBUG calls produce no output).
3. ``bind_task_context`` merges contextvars into output; ``clear_task_context``
   isolates subsequent calls.
4. ``task_logger`` writes JSONL to a per-task file with no cross-contamination.
5. The module itself respects the D-22 async-open guard.

Reference: 03-01-PLAN.md Task 1 <behavior>; 03-CONTEXT.md D-36, D-37, D-38.
"""
from __future__ import annotations

import io
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
import structlog

from finance_skills_mcp.logging_config import (
    _reset_for_tests,
    bind_task_context,
    clear_task_context,
    configure_logging,
    task_logger,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_logging_between_tests():
    """Reset the module-level _CONFIGURED flag before each test.

    Without this, the second test in this module would silently observe
    the configuration from the first test (idempotency is a feature in
    production, but tests need to re-exercise the wiring path).
    """
    _reset_for_tests()
    yield
    _reset_for_tests()
    # Always clear contextvars so a test that bound them does not leak
    # into the next.
    structlog.contextvars.clear_contextvars()


def test_configure_logging_installs_json_renderer():
    """After configure_logging(), a single log call produces one JSON line."""
    stream = io.StringIO()
    configure_logging(stream=stream)
    log = structlog.get_logger("test")
    log.info("evt", k="v")
    output = stream.getvalue().strip()
    assert output, "configure_logging should have produced one log line"
    # Single line — no embedded newline inside the JSON object.
    assert "\n" not in output, (
        f"expected single-line JSON, got multi-line: {output!r}"
    )
    parsed = json.loads(output)
    assert parsed["event"] == "evt", f"event field wrong: {parsed!r}"
    assert parsed["k"] == "v", f"custom field missing: {parsed!r}"
    # Pipeline must have added level + timestamp.
    assert parsed["level"] == "info", f"level missing: {parsed!r}"
    assert "timestamp" in parsed, f"timestamp missing: {parsed!r}"


def test_default_level_is_info():
    """DEBUG calls produce no output; INFO produces exactly one line (D-36)."""
    stream = io.StringIO()
    configure_logging(stream=stream)  # Default level = INFO.
    log = structlog.get_logger("test")
    log.debug("should_not_appear", k="v")
    assert stream.getvalue() == "", (
        f"DEBUG-level call leaked to output at default INFO level: "
        f"{stream.getvalue()!r}"
    )
    log.info("should_appear", k="v")
    output = stream.getvalue().strip()
    assert output, "INFO-level call must produce output at default level"
    parsed = json.loads(output)
    assert parsed["event"] == "should_appear"


def test_contextvars_merge():
    """bind/clear isolate per-task fields across calls (D-38)."""
    stream = io.StringIO()
    configure_logging(stream=stream)
    log = structlog.get_logger("test")

    bind_task_context(
        task_id="20260520T120000-deadbeef",
        tool_name="create_task",
        skill_ids=["a", "b"],
    )
    log.info("task_started")
    line1 = stream.getvalue().strip()
    assert line1, "expected one log line after bind"
    parsed1 = json.loads(line1)
    assert parsed1["task_id"] == "20260520T120000-deadbeef", (
        f"task_id missing or wrong: {parsed1!r}"
    )
    assert parsed1["tool_name"] == "create_task", f"tool_name wrong: {parsed1!r}"
    assert parsed1["skill_ids"] == ["a", "b"], f"skill_ids wrong: {parsed1!r}"

    # Now clear; the next log line must not carry those fields.
    clear_task_context()
    stream.seek(0)
    stream.truncate()
    log.info("after_clear")
    line2 = stream.getvalue().strip()
    parsed2 = json.loads(line2)
    assert "task_id" not in parsed2, (
        f"task_id leaked across clear: {parsed2!r}"
    )
    assert "tool_name" not in parsed2, (
        f"tool_name leaked across clear: {parsed2!r}"
    )
    assert "skill_ids" not in parsed2, (
        f"skill_ids leaked across clear: {parsed2!r}"
    )


def test_per_task_file_logger(tmp_path: Path):
    """task_logger writes JSONL to the requested path; two paths stay isolated."""
    log_path_a = tmp_path / "task-a" / "logs" / "server.jsonl"
    log_path_b = tmp_path / "task-b" / "logs" / "server.jsonl"
    log_path_a.parent.mkdir(parents=True)
    log_path_b.parent.mkdir(parents=True)

    logger_a, fh_a = task_logger(log_path_a)
    logger_b, fh_b = task_logger(log_path_b)
    try:
        logger_a.info("event_a", x=1)
        logger_a.info("event_a2", x=2)
        logger_b.info("event_b", y=10)
    finally:
        fh_a.close()
        fh_b.close()

    text_a = log_path_a.read_text(encoding="utf-8")
    text_b = log_path_b.read_text(encoding="utf-8")

    # Each line must parse as JSON, and there must be a trailing newline.
    assert text_a.endswith("\n"), (
        f"task-a log missing trailing newline: {text_a!r}"
    )
    lines_a = [line for line in text_a.splitlines() if line]
    assert len(lines_a) == 2, (
        f"task-a expected 2 lines, got {len(lines_a)}: {lines_a!r}"
    )
    parsed_a = [json.loads(line) for line in lines_a]
    assert parsed_a[0]["event"] == "event_a"
    assert parsed_a[0]["x"] == 1
    assert parsed_a[1]["event"] == "event_a2"
    assert parsed_a[1]["x"] == 2

    lines_b = [line for line in text_b.splitlines() if line]
    assert len(lines_b) == 1, (
        f"task-b expected 1 line, got {len(lines_b)}: {lines_b!r}"
    )
    parsed_b = json.loads(lines_b[0])
    assert parsed_b["event"] == "event_b"
    assert parsed_b["y"] == 10
    # Cross-contamination guard: task-b log must NOT contain any of
    # task-a's payloads.
    assert "event_a" not in text_b, (
        f"task-a event leaked into task-b log: {text_b!r}"
    )
    assert "event_a2" not in text_b


def test_no_bare_open_in_module():
    """Module respects the D-22 async-open AST guard.

    Invokes ``scripts/ci/forbid_async_open.py`` against the module path
    and asserts a 0 exit code. The module's only ``open()`` call lives
    inside the sync function ``task_logger`` — callers must wrap it in
    ``anyio.to_thread.run_sync(...)`` from any async site.
    """
    module_path = REPO_ROOT / "src" / "finance_skills_mcp" / "logging_config.py"
    # The CI script takes a directory; we point it at the module's parent
    # but constrain to a single file via an isolated tmp_path-free
    # approach: call it on the module's parent and verify the module
    # path does not appear in the violation output.
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "ci" / "forbid_async_open.py"),
            str(module_path.parent),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"forbid_async_open.py reported violations in src/finance_skills_mcp/:\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    # Belt-and-braces: ensure no violation line names logging_config.py.
    assert "logging_config.py" not in result.stderr, (
        f"logging_config.py flagged by D-22 guard: {result.stderr!r}"
    )
