"""Task lifecycle orchestration (D-04, D-10, D-11, D-22, D-23, D-24, EXEC-04, EXEC-07).

The try/finally around lock release is sacred — release MUST happen on every
exit path (success, validation failure, timeout, agent exception, server
runtime error). All blocking I/O is wrapped in ``anyio.to_thread.run_sync``
(EXEC-07 / D-22).

EXEC-04 invariant: ``output.md`` is written FIRST, THEN ``status.json``
flips to a terminal state. Plan 01-04 has an independent test that fails if
the order is reversed. Do NOT inline this — the order MUST be visible in
``create()``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

import anyio
import structlog

from finance_skills_mcp import errors
from finance_skills_mcp.ids import _new_task_id
from finance_skills_mcp.lock_manager import BusyError
from finance_skills_mcp.logging_config import (
    bind_task_context,
    clear_task_context,
    task_logger,
)

log = logging.getLogger("finance_skills_mcp.task_manager")
# Per-D-37: stderr/global structured logger surfaces non-per-task events
# (and pre-file lifecycle events that are also replayed into the per-task
# server.jsonl once it exists).
_slog = structlog.get_logger("finance_skills_mcp.task_manager")

TASK_TIMEOUT_SECONDS = float(os.environ.get("FSM_TASK_TIMEOUT_SECONDS", "600"))  # D-11
MAX_PROMPT_BYTES = 102_400  # D-23 (specs/create-task/spec.md INVALID_PROMPT)


async def _emit_tlog(tlog: Any, event: str, **kw: Any) -> None:
    """Emit a per-task structlog line off the event loop (D-22 strict).

    The per-task ``server.jsonl`` handle is line-buffered (see
    ``logging_config.task_logger``); each ``PrintLogger.msg()`` call performs
    a synchronous ``file.write(...)`` that flushes on the trailing newline.
    Under nominal local-disk conditions every write is sub-millisecond, but
    under stalled-disk pathologies (network-mounted ``tasks/``, snapshot
    freeze) a sync write would block the asyncio loop and breach the SC2
    "polls return within 200 ms" invariant DEPLOY.md re-asserts.

    Routing every emission through ``anyio.to_thread.run_sync`` keeps the
    D-22 async-I/O guard strict: ALL blocking file writes — open, close,
    AND every line emission — hop a worker thread. ``tlog`` may be ``None``
    when the per-task log open failed (line 182 path); the helper no-ops in
    that case so the call site stays unconditional.

    Failures are swallowed (best-effort): log emission must NEVER re-raise
    into the lifecycle path. The global stderr logger already carries the
    pre-file events via the buffer-and-replay pattern, so a per-task write
    failure is recoverable noise, not lost information.
    """
    if tlog is None:
        return
    try:
        await anyio.to_thread.run_sync(lambda: tlog.info(event, **kw))
    except Exception:  # noqa: BLE001 — log emission must never re-raise
        pass


class TaskManager:
    """Orchestrates the validate → lock → write → run → persist → release pipeline."""

    def __init__(
        self,
        catalog,
        lock_mgr,
        tasks_root: Path,
        repo_root: Path,
        agent_runner_module,
        task_store_module,
        skill_roots: tuple[Path, ...] | None = None,
    ):
        self.catalog = catalog
        self.lock_mgr = lock_mgr
        self.tasks_root = Path(tasks_root)
        self.repo_root = Path(repo_root)
        # Phase 2 (D-23 / M-5 of 02-01): the indexer stores ``Skill.path``
        # relative to the SCAN ROOT that produced the entry, not relative
        # to ``repo_root``. ``stage_skills_in_workspace`` therefore needs
        # the scan-roots list to resolve a relative ``Skill.path`` into an
        # absolute on-disk source directory. ``skill_roots`` defaults to
        # ``(repo_root,)`` for backward compatibility with any caller that
        # still treats ``Skill.path`` as relative to repo_root.
        self.skill_roots: tuple[Path, ...] = (
            tuple(Path(r) for r in skill_roots)
            if skill_roots
            else (self.repo_root,)
        )
        # DI seam: agent_runner and task_store are module references so
        # MockAgentRunner / test doubles can be substituted via mocker.patch.
        self.agent_runner = agent_runner_module
        self.task_store = task_store_module

    # ---------------------------------------------------------------------
    # create_task
    # ---------------------------------------------------------------------
    async def create(self, prompt: str, skills: list[str]):
        """Orchestrate one task end-to-end. Returns success dict OR ErrorToolResult."""
        # 1. Validate input BEFORE lock (D-23 — no lock acquired on validation errors)
        if not isinstance(prompt, str) or prompt == "":
            return errors.validation_error(
                "INVALID_PROMPT", "prompt must be a non-empty string"
            )
        if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            return errors.validation_error(
                "INVALID_PROMPT",
                f"prompt exceeds {MAX_PROMPT_BYTES} bytes",
            )
        if not isinstance(skills, list):
            return errors.validation_error(
                "INVALID_PROMPT", "skills must be a list of skill ids"
            )
        valid_skill_ids = {s.id for s in self.catalog.skills}
        for s in skills:
            if s not in valid_skill_ids:
                return errors.validation_error(
                    "UNKNOWN_SKILL", f"Unknown skill: {s}"
                )

        # 2. Try lock (non-blocking)
        task_id = _new_task_id()
        started_at = datetime.now(timezone.utc).isoformat()
        started_perf = anyio.current_time()

        # D-38: bind the three mandatory per-task fields into the
        # structlog contextvars scope so every subsequent log call carries
        # them automatically. The clear_task_context() call in the
        # outer-finally below is the lifecycle counterpart.
        bind_task_context(
            task_id=task_id, tool_name="create_task", skill_ids=list(skills)
        )

        # M-1 (03-PLAN-CHECK): the per-task ``server.jsonl`` does not
        # exist yet because ``create_task_dirs`` runs in step 3 below.
        # Events that fire BEFORE step 3 (task_started, task_lock_acquired)
        # are buffered here and replayed into the file logger once it
        # opens. Until then, they are also emitted to the global stderr
        # logger so the start of every task is visible even if dir
        # creation crashes.
        early_events: list[tuple[str, dict[str, Any]]] = []

        # task_started — emitted BEFORE try_acquire so a future BUSY-path
        # instrumentation point (D-42 disk_precheck_refused) inherits the
        # same buffer pattern.
        early_events.append(("task_started", {}))
        _slog.info("task_started")

        try:
            await self.lock_mgr.try_acquire(
                task_id=task_id, started_at_iso=started_at
            )
        except BusyError as e:
            clear_task_context()
            return errors.busy_error(
                inflight_task_id=e.inflight_task_id,
                started_at=e.started_at,
            )

        early_events.append(("task_lock_acquired", {}))
        _slog.info("task_lock_acquired")

        # 3..N inside try/finally — release no matter what.
        # ``tlog`` (the per-task structlog logger) and ``tlog_fh`` (the
        # underlying file handle) are bound inside step 3a once the dirs
        # exist; both are closed/cleared in the outer finally.
        tlog: Any = None
        tlog_fh: IO[str] | None = None
        try:
            # 3. Create task dirs (workspace + logs)
            try:
                task_dir = await anyio.to_thread.run_sync(
                    self.task_store.create_task_dirs,
                    self.tasks_root,
                    task_id,
                )
            except (OSError, ValueError) as exc:
                log.exception("Failed to create task directory")
                _slog.error(
                    "task_failed",
                    status="failed",
                    error_class=type(exc).__name__,
                    error_reason="storage_error_create_dirs",
                )
                return errors.validation_error(
                    "STORAGE_ERROR",
                    f"Failed to create task directory: {exc}",
                )

            # 3a. Open the per-task structlog file logger (D-37). The open
            # is blocking and hops a worker thread per D-22. An OSError
            # here downgrades to stderr-only — the task continues to run,
            # the operator just loses the per-task JSONL file for this run.
            log_path = task_dir / "logs" / "server.jsonl"
            try:
                tlog, tlog_fh = await anyio.to_thread.run_sync(
                    task_logger, log_path
                )
            except OSError:
                log.exception("Failed to open per-task log file at %s", log_path)
                tlog = None
                tlog_fh = None

            # 3b. Replay the pre-file event buffer into the per-task file
            # logger in original order. Test 3 in 03-01-PLAN.md asserts
            # this is what makes ``task_started`` + ``task_lock_acquired``
            # visible in the per-task ``server.jsonl``. Each replay goes
            # through ``_emit_tlog`` so the sync ``PrintLogger.msg()`` write
            # hops a worker thread (D-22 strict — see helper docstring).
            if tlog is not None:
                for evt, kw in early_events:
                    await _emit_tlog(tlog, evt, **kw)
                early_events.clear()

            # 4. Atomic-write input.md + initial status.json
            await anyio.to_thread.run_sync(
                self.task_store.atomic_write_text,
                task_dir / "input.md",
                prompt,
            )

            # 4b. Stage requested skills under <workspace>/.claude/skills/<name>/
            # so the SDK discovers them (D-15/D-17 workspace prep). This is the
            # mechanism by which Phase-2 indexed skills become visible to the
            # SDK; without it the SDK reports "No project skills found" and the
            # agent ignores the SKILL.md body. Skill identity is opaque to this
            # module per UNIV-03 — no skill name appears as a literal here.
            skill_entries = [
                (s.name, s.path)
                for s in self.catalog.skills
                if s.id in set(skills)
            ]
            try:
                await anyio.to_thread.run_sync(
                    self.task_store.stage_skills_in_workspace,
                    task_dir / "workspace",
                    self.repo_root,
                    skill_entries,
                    self.skill_roots,
                )
            except (OSError, FileNotFoundError, ValueError) as exc:
                log.exception("Failed to stage skills in workspace")
                return errors.validation_error(
                    "STORAGE_ERROR",
                    f"Failed to stage skill files: {exc}",
                )
            initial_status = {
                "task_id": task_id,
                "status": "working",
                "started_at": started_at,
                "elapsed_seconds": 0.0,
            }
            await anyio.to_thread.run_sync(
                self.task_store.atomic_write_json,
                task_dir / "status.json",
                initial_status,
            )

            # 5. Run agent with hard timeout (D-11)
            workspace = task_dir / "workspace"
            output_text: str
            terminal: str
            error_reason: str | None = None
            error_class: str | None = None

            # D-39: agent_call_started fires immediately BEFORE wait_for.
            # The prompt is NEVER logged (T-03-01-04 mitigation) — only
            # the structural fact that the call is about to begin.
            agent_call_started = anyio.current_time()
            await _emit_tlog(tlog, "agent_call_started")

            try:
                output_text = await asyncio.wait_for(
                    self.agent_runner.run(
                        prompt=prompt, skills=skills, cwd=workspace
                    ),
                    timeout=TASK_TIMEOUT_SECONDS,
                )
                terminal = "completed"
                agent_call_outcome = "returned"
            except asyncio.TimeoutError:
                output_text = (
                    f"Task exceeded {TASK_TIMEOUT_SECONDS}s timeout and was cancelled.\n"
                )
                terminal = "failed"
                error_reason = "timeout"
                error_class = "TimeoutError"
                agent_call_outcome = "timeout"
            except Exception as exc:  # noqa: BLE001 — surface SDK errors as failed
                log.exception("Agent execution raised")
                output_text = (
                    f"Agent execution raised: {type(exc).__name__}: {exc}\n"
                )
                terminal = "failed"
                error_reason = "agent_error"
                error_class = type(exc).__name__
                agent_call_outcome = "raised"

            # D-39: agent_call_returned fires immediately AFTER the agent
            # call returns, raises, or times out — every path. The
            # elapsed_ms carries durative cost info for operators.
            agent_elapsed_ms = int(
                (anyio.current_time() - agent_call_started) * 1000
            )
            await _emit_tlog(
                tlog,
                "agent_call_returned",
                elapsed_ms=agent_elapsed_ms,
                status=agent_call_outcome,
            )

            # 6. Atomic-write output.md FIRST (EXEC-04 order — sacred)
            await anyio.to_thread.run_sync(
                self.task_store.atomic_write_text,
                task_dir / "output.md",
                output_text,
            )

            # 7. THEN flip status.json to terminal state
            finished_at = datetime.now(timezone.utc).isoformat()
            elapsed = anyio.current_time() - started_perf
            final_status: dict[str, Any] = {
                "task_id": task_id,
                "status": terminal,
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_seconds": elapsed,
            }
            if error_reason:
                final_status["error_reason"] = error_reason
            await anyio.to_thread.run_sync(
                self.task_store.atomic_write_json,
                task_dir / "status.json",
                final_status,
            )

            # D-39: task_completed / task_failed AFTER the terminal
            # status.json write. Order matters — the on-disk truth
            # exists before the log line claims it.
            if terminal == "completed":
                await _emit_tlog(tlog, "task_completed", status="completed")
            else:
                await _emit_tlog(
                    tlog,
                    "task_failed",
                    status="failed",
                    error_class=error_class or "Unknown",
                    error_reason=error_reason or "unknown",
                )
        finally:
            # 6. Lock release first (sacred contract from Phase 1) then
            # D-39 task_lock_released to the per-task logger.
            await self.lock_mgr.release()
            # ``_emit_tlog`` already swallows exceptions and no-ops on
            # ``tlog is None`` so the finally path stays clean.
            await _emit_tlog(tlog, "task_lock_released")
            # 7. Close the per-task file handle on a worker thread (D-22).
            if tlog_fh is not None:
                try:
                    await anyio.to_thread.run_sync(tlog_fh.close)
                except Exception:  # noqa: BLE001 — best effort
                    pass
            # 8. Release the per-task contextvars binding so the next
            # incoming request starts with a clean scope.
            clear_task_context()

        # Success: return the task_id as structured content.
        return {"task_id": task_id}

    # ---------------------------------------------------------------------
    # get_task_status
    # ---------------------------------------------------------------------
    async def get_status(self, task_id: str):
        """Read status.json. Returns the JSON payload or TASK_NOT_FOUND."""
        try:
            self.task_store.validate_task_id(task_id)
        except ValueError:
            return errors.validation_error(
                "TASK_NOT_FOUND", f"Invalid task_id format: {task_id}",
                task_id=task_id,
            )

        try:
            payload = await anyio.to_thread.run_sync(
                self.task_store.read_status_json, self.tasks_root, task_id
            )
        except FileNotFoundError:
            return errors.task_not_found(task_id)

        # Compute live elapsed_seconds while task is still working (clients
        # poll repeatedly, so the on-disk elapsed is stale until the task
        # terminates).
        if payload.get("status") == "working":
            started_at = payload.get("started_at")
            try:
                started_dt = datetime.fromisoformat(
                    started_at.replace("Z", "+00:00")
                )
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                payload["elapsed_seconds"] = max(
                    0.0,
                    (datetime.now(timezone.utc) - started_dt).total_seconds(),
                )
            except (AttributeError, ValueError):
                pass

        return payload

    # ---------------------------------------------------------------------
    # get_task_result
    # ---------------------------------------------------------------------
    async def get_result(self, task_id: str):
        """Return output.md + metadata for a terminal task; D-24 error otherwise."""
        try:
            self.task_store.validate_task_id(task_id)
        except ValueError:
            return errors.validation_error(
                "TASK_NOT_FOUND", f"Invalid task_id format: {task_id}",
                task_id=task_id,
            )

        try:
            payload = await anyio.to_thread.run_sync(
                self.task_store.read_status_json, self.tasks_root, task_id
            )
        except FileNotFoundError:
            return errors.task_not_found(task_id)

        status = payload.get("status")
        if status not in {"completed", "failed"}:
            return errors.task_not_terminal(
                task_id=task_id, current_status=status or "unknown"
            )

        # Read output.md
        def _read_output() -> str:
            d = self.task_store.task_dir(self.tasks_root, task_id)
            output_path = d / "output.md"
            return output_path.read_text(encoding="utf-8")

        try:
            output_markdown = await anyio.to_thread.run_sync(_read_output)
        except FileNotFoundError:
            output_markdown = ""

        return {
            "output_markdown": output_markdown,
            "metadata": payload,
        }
