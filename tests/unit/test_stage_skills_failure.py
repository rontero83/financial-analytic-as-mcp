"""WR-02 — stage_skills_in_workspace failure emits ``task_failed`` event.

When ``TaskManager.create`` cannot stage a requested skill into the task's
workspace (the ``stage_skills_in_workspace`` call raises ``OSError`` or
``ValueError``), the caller used to receive a ``STORAGE_ERROR`` MCP result
with NO corresponding structured ``task_failed`` line on stderr. Operators
running ``grep '"event":"task_failed"' server.stderr`` therefore could not
see the failure at all — the sibling ``create_task_dirs`` failure block
DOES emit such a line, so the log surface was asymmetric.

This test fixes the surface contract by asserting:

1. The STORAGE_ERROR return shape stays intact (caller contract preserved).
2. A structured ``task_failed`` line is emitted to stderr with the documented
   ``error_class`` / ``error_reason="storage_error_stage_skills"`` payload.

Mutation proof: removing the new ``_slog.error("task_failed", ...)`` block
in ``task_manager.py`` makes the second assertion fail with "no task_failed
event emitted".

Reference: 03-REVIEW.md WR-02; 03-CONTEXT.md D-39 (events list).
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog

from finance_skills_mcp import task_store
from finance_skills_mcp.lock_manager import LockManager
from finance_skills_mcp.logging_config import (
    _reset_for_tests,
    configure_logging,
)
from finance_skills_mcp.skill_catalog import Catalog, Skill
from finance_skills_mcp.task_manager import TaskManager


@pytest.fixture(autouse=True)
def _restore_logging_after_test():
    """Re-wire structlog to sys.stderr after this test so the
    session-scoped fixture's contract holds for the next test."""
    yield
    _reset_for_tests()
    configure_logging()
    structlog.contextvars.clear_contextvars()


@pytest.mark.anyio
async def test_stage_skills_failure_emits_task_failed_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSError raised inside stage_skills_in_workspace produces a structured
    ``task_failed`` line AND still returns STORAGE_ERROR to the caller."""
    # --- isolate logging into a tmp StringIO sink we control ---
    # Background: the session-scoped autouse fixture in
    # integration_in_memory/conftest.py may have already called
    # ``configure_logging()`` and bound the global structlog pipeline to
    # ``sys.stderr`` captured at that moment. structlog's
    # ``cache_logger_on_first_use=True`` then froze the
    # ``finance_skills_mcp.task_manager._slog`` logger against THAT
    # ``sys.stderr`` reference, so neither ``capsys`` (which swaps
    # ``sys.stderr`` at request time) nor a plain ``configure_logging(
    # stream=...)`` re-call (which is a no-op when ``_CONFIGURED`` is True
    # AND useless against the cached logger anyway) can redirect it.
    #
    # ``_reset_for_tests()`` calls ``structlog.reset_defaults()`` which
    # drops the cached BoundLogger, and flips ``_CONFIGURED`` back to
    # False; the subsequent ``configure_logging(stream=stream)`` then
    # rebinds the pipeline to our StringIO sink.
    _reset_for_tests()
    stream = io.StringIO()
    configure_logging(level=logging.INFO, stream=stream)
    # Belt-and-braces: contextvars may carry over from a prior test in the
    # same session; clear them so the new task starts with a clean scope.
    structlog.contextvars.clear_contextvars()
    # The task_manager module bound ``_slog`` at import time. After
    # ``reset_defaults`` the cached bound logger is gone — but the module
    # attribute still references the OLD lazy-proxy. Replace it with a
    # freshly-resolved logger so the new test sees the new pipeline. The
    # original is restored at test teardown via monkeypatch.
    import finance_skills_mcp.task_manager as tm_module
    monkeypatch.setattr(
        tm_module,
        "_slog",
        structlog.get_logger("finance_skills_mcp.task_manager"),
    )

    # --- minimal environment ---
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    repo_root = tmp_path

    fixture_skill_path = tmp_path / "fixture-skill"
    fixture_skill_path.mkdir()
    (fixture_skill_path / "SKILL.md").write_text(
        "---\nname: fixture-stage-fail\n---\nfixture body\n",
        encoding="utf-8",
    )
    catalog = Catalog(
        skills=(
            Skill(
                id="fixture-stage-fail",
                name="fixture-stage-fail",
                description="WR-02 stage-failure fixture.",
                path="fixture-skill",
            ),
        )
    )
    lock_mgr = LockManager(tasks_root=tasks_root)

    async def det_runner(prompt: str, skills, cwd: Path) -> str:
        return "should-not-reach-here"

    agent_runner_module = SimpleNamespace(run=det_runner)

    # --- monkey-patch stage_skills_in_workspace to raise an OSError ---
    def exploding_stage(workspace_dir, repo_root_arg, skill_entries, skill_roots):
        raise OSError("simulated staging failure (e.g. cross-device link)")

    monkeypatch.setattr(
        task_store, "stage_skills_in_workspace", exploding_stage
    )

    task_mgr = TaskManager(
        catalog=catalog,
        lock_mgr=lock_mgr,
        tasks_root=tasks_root,
        repo_root=repo_root,
        agent_runner_module=agent_runner_module,
        task_store_module=task_store,
    )

    result = await task_mgr.create(
        prompt="stage failure test",
        skills=["fixture-stage-fail"],
    )

    # --- assertion 1: caller contract preserved (STORAGE_ERROR shape) ---
    # ``errors.validation_error("STORAGE_ERROR", ...)`` returns an
    # ``ErrorToolResult`` (FastMCP wrapper) carrying
    # ``meta={"error_code": "STORAGE_ERROR"}``. We assert on the error_code
    # rather than the exact wrapper class so the test tolerates internal
    # refactors of the error helper.
    meta = getattr(result, "meta", None) or {}
    error_code = meta.get("error_code")
    assert error_code == "STORAGE_ERROR", (
        f"expected error_code=STORAGE_ERROR, got result={result!r}"
    )

    # --- assertion 2: a structured task_failed line was emitted ---
    raw_lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    task_failed_lines: list[dict] = []
    for line in raw_lines:
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if parsed.get("event") == "task_failed":
            task_failed_lines.append(parsed)

    assert task_failed_lines, (
        "WR-02 violation: stage_skills_in_workspace failure emitted no "
        "structured task_failed event. stderr stream was:\n"
        + "\n".join(raw_lines)
    )

    # Find the staging-failure line specifically (there may also be a
    # create_dirs-failure line under other test conditions; we are
    # asserting on THIS path).
    stage_lines = [
        p for p in task_failed_lines
        if p.get("error_reason") == "storage_error_stage_skills"
    ]
    assert stage_lines, (
        "WR-02 violation: no task_failed line with "
        "error_reason='storage_error_stage_skills'. Saw: "
        f"{task_failed_lines!r}"
    )

    payload = stage_lines[0]
    assert payload.get("status") == "failed", (
        f"task_failed line should carry status='failed': {payload!r}"
    )
    assert payload.get("error_class") == "OSError", (
        f"task_failed line should carry error_class='OSError' "
        f"(the raised exception type): {payload!r}"
    )
