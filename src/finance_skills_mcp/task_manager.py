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
from typing import Any

import anyio

from finance_skills_mcp import errors
from finance_skills_mcp.ids import _new_task_id
from finance_skills_mcp.lock_manager import BusyError

log = logging.getLogger("finance_skills_mcp.task_manager")

TASK_TIMEOUT_SECONDS = float(os.environ.get("FSM_TASK_TIMEOUT_SECONDS", "600"))  # D-11
MAX_PROMPT_BYTES = 102_400  # D-23 (specs/create-task/spec.md INVALID_PROMPT)


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
    ):
        self.catalog = catalog
        self.lock_mgr = lock_mgr
        self.tasks_root = Path(tasks_root)
        self.repo_root = Path(repo_root)
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
        try:
            await self.lock_mgr.try_acquire(
                task_id=task_id, started_at_iso=started_at
            )
        except BusyError as e:
            return errors.busy_error(
                inflight_task_id=e.inflight_task_id,
                started_at=e.started_at,
            )

        # 3..N inside try/finally — release no matter what
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
                return errors.validation_error(
                    "STORAGE_ERROR",
                    f"Failed to create task directory: {exc}",
                )

            # 4. Atomic-write input.md + initial status.json
            await anyio.to_thread.run_sync(
                self.task_store.atomic_write_text,
                task_dir / "input.md",
                prompt,
            )

            # 4b. Stage requested skills under <workspace>/.claude/skills/<name>/
            # so the SDK discovers them (D-15/D-17 workspace prep). This is the
            # mechanism by which fixture-skill-alpha (and Phase 2's real skills)
            # become visible to the SDK; without it the SDK reports
            # "No project skills found" and the agent ignores the SKILL.md body.
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
            try:
                output_text = await asyncio.wait_for(
                    self.agent_runner.run(
                        prompt=prompt, skills=skills, cwd=workspace
                    ),
                    timeout=TASK_TIMEOUT_SECONDS,
                )
                terminal = "completed"
            except asyncio.TimeoutError:
                output_text = (
                    f"Task exceeded {TASK_TIMEOUT_SECONDS}s timeout and was cancelled.\n"
                )
                terminal = "failed"
                error_reason = "timeout"
            except Exception as exc:  # noqa: BLE001 — surface SDK errors as failed
                log.exception("Agent execution raised")
                output_text = (
                    f"Agent execution raised: {type(exc).__name__}: {exc}\n"
                )
                terminal = "failed"
                error_reason = "agent_error"

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
        finally:
            await self.lock_mgr.release()

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
