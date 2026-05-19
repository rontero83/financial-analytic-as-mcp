"""Single-task lock + startup recovery (D-06..D-09, D-22).

Lock primitives:
- ``asyncio.Lock`` — in-process fast-path BUSY gate (D-06).
- ``filelock.AsyncFileLock`` — cross-process / crash-recovery source of truth.

Acquire order (refinement of CONTEXT.md <specifics>): ``asyncio.Lock`` FIRST
(cheap in-memory check before any disk syscall), ``AsyncFileLock`` SECOND
(blocking=False; raises ``filelock.Timeout`` immediately on contention).
Release order: AsyncFileLock FIRST, ``asyncio.Lock`` SECOND.

The ``tasks/.lock`` payload (D-07) is a JSON blob: ``{pid, hostname, task_id,
started_at, last_heartbeat}``. The heartbeat coroutine refreshes
``last_heartbeat`` every 5 s (D-07) and is cancelled at release. Heartbeat
write failures are logged WARNING and never propagate (Pitfall 7).

Startup recovery (D-08): malformed JSON → reconcile + log; different
hostname or alive PID → fail-fast; dead PID (ESRCH) or stale heartbeat
(>15 s old) on same host → mark orphan ``failed: server_restart`` and
release.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anyio
from filelock import AsyncFileLock, Timeout

from finance_skills_mcp import task_store

log = logging.getLogger("finance_skills_mcp.lock_manager")

HEARTBEAT_INTERVAL_SECONDS = 5  # D-07
HEARTBEAT_STALE_THRESHOLD_SECONDS = 15  # CONTEXT.md <specifics>
LOCK_FILE_NAME = ".lock"


class BusyError(Exception):
    """Raised when the lock cannot be acquired (another task in flight).

    The caller in ``task_manager.create`` converts this into the BUSY
    structured error wire shape per D-23.
    """

    def __init__(self, inflight_task_id: str, started_at: str):
        super().__init__(f"Task {inflight_task_id} already in flight")
        self.inflight_task_id = inflight_task_id
        self.started_at = started_at


class LockManager:
    """Coordinates the in-process + cross-process locks for single-task semantics."""

    def __init__(self, tasks_root: Path):
        self.tasks_root = Path(tasks_root)
        self.lock_path = self.tasks_root / LOCK_FILE_NAME
        # AsyncFileLock uses its own sentinel sibling file — keep our payload
        # file (`.lock`) separate from the AsyncFileLock sentinel (`.lock.filelock`).
        self._file_lock = AsyncFileLock(str(self.lock_path) + ".filelock")
        self._in_proc_lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._current_task_id: Optional[str] = None
        self._current_payload: Optional[dict] = None

    # ---------------------------------------------------------------------
    # Startup recovery (D-08)
    # ---------------------------------------------------------------------
    async def startup_recovery(self) -> None:
        """Reconcile any pre-existing ``tasks/.lock`` payload from a prior server.

        Cases:
        - File absent → no-op.
        - File present, JSON malformed → log WARNING, reconcile (orphan handling
          may skip without a known task_id), unlink the payload.
        - File present, ``hostname != socket.gethostname()`` → fail-fast
          (another machine appears to hold the lock).
        - File present, PID alive on same host → fail-fast
          ("Server already running on this host"). PermissionError on
          ``os.kill(pid, 0)`` is treated as alive (safer to refuse than crash).
        - File present, PID dead (ESRCH) → reconcile, log WARNING.
        - File present, heartbeat ``> HEARTBEAT_STALE_THRESHOLD_SECONDS`` old
          AND same host → treat as dead, reconcile, log WARNING.
        """
        lock_payload_path = self.lock_path
        if not lock_payload_path.is_file():
            return

        def _read_payload_bytes() -> str:
            return lock_payload_path.read_text(encoding="utf-8")

        try:
            raw = await anyio.to_thread.run_sync(_read_payload_bytes)
            payload = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning(
                "tasks/.lock is malformed (%s); reconciling and removing",
                type(exc).__name__,
            )
            await self._reconcile_orphan(payload=None)
            return

        pid = payload.get("pid")
        hostname = payload.get("hostname")

        if not isinstance(pid, int) or not isinstance(hostname, str):
            log.warning("tasks/.lock payload missing pid/hostname; reconciling")
            await self._reconcile_orphan(payload=payload)
            return

        local_host = socket.gethostname()
        if hostname != local_host:
            raise RuntimeError(
                f"tasks/.lock held by PID {pid} on host {hostname!r} "
                f"(this is {local_host!r}). Refusing to start a second server."
            )

        # Same host → check liveness.
        try:
            os.kill(pid, 0)
            pid_alive = True
        except ProcessLookupError:
            pid_alive = False
        except PermissionError:
            # Process exists but is owned by another user — assume alive.
            pid_alive = True

        if pid_alive:
            # Could still be a hung process — check heartbeat staleness.
            if self._heartbeat_is_stale(payload):
                log.warning(
                    "tasks/.lock owner PID %d appears alive but heartbeat is "
                    "stale (> %ds old); treating as dead and reconciling",
                    pid,
                    HEARTBEAT_STALE_THRESHOLD_SECONDS,
                )
                await self._reconcile_orphan(payload=payload)
                return
            raise RuntimeError(
                f"Server already running on this host (PID {pid}). "
                "Refusing to start a second server."
            )

        # PID is dead → reconcile.
        log.warning(
            "tasks/.lock owner PID %d is gone; reconciling orphan task %s",
            pid,
            payload.get("task_id"),
        )
        await self._reconcile_orphan(payload=payload)

    @staticmethod
    def _heartbeat_is_stale(payload: dict) -> bool:
        ts = payload.get("last_heartbeat")
        if not isinstance(ts, str):
            return True
        try:
            hb = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return True
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - hb).total_seconds()
        return age > HEARTBEAT_STALE_THRESHOLD_SECONDS

    async def _reconcile_orphan(self, payload: Optional[dict]) -> None:
        """Mark the orphan task ``failed: server_restart`` (best-effort), unlink lock."""
        if payload:
            task_id = payload.get("task_id")
            if isinstance(task_id, str):
                try:
                    task_id_dir = task_store.task_dir(self.tasks_root, task_id)
                    started_at = payload.get("started_at", "")
                    fail_payload = {
                        "task_id": task_id,
                        "status": "failed",
                        "started_at": started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "elapsed_seconds": 0.0,
                        "error_reason": "server_restart",
                    }
                    await anyio.to_thread.run_sync(
                        task_store.atomic_write_json,
                        task_id_dir / "status.json",
                        fail_payload,
                    )
                    await anyio.to_thread.run_sync(
                        task_store.atomic_write_text,
                        task_id_dir / "output.md",
                        (
                            f"Task interrupted by server restart at "
                            f"{fail_payload['finished_at']}.\n"
                        ),
                    )
                except Exception:
                    log.exception(
                        "Failed to mark orphan task %s as failed:server_restart",
                        task_id,
                    )

        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass

    # ---------------------------------------------------------------------
    # try_acquire / release
    # ---------------------------------------------------------------------
    async def try_acquire(self, task_id: str, started_at_iso: str) -> None:
        """Acquire both locks non-blocking; raise ``BusyError`` on contention.

        Lock order: ``asyncio.Lock`` first (cheap), AsyncFileLock second.
        Refinement of CONTEXT.md <specifics> per plan-body <assumptions>.
        """
        # Step 1: in-process lock fast path
        if self._in_proc_lock.locked():
            inflight_id, inflight_started = self._read_current_payload()
            raise BusyError(
                inflight_task_id=inflight_id,
                started_at=inflight_started,
            )
        await self._in_proc_lock.acquire()

        try:
            # Step 2: cross-process file lock
            try:
                await self._file_lock.acquire(blocking=False)
            except Timeout:
                inflight_id, inflight_started = self._read_current_payload()
                raise BusyError(
                    inflight_task_id=inflight_id,
                    started_at=inflight_started,
                )

            # Step 3: write OUR payload to tasks/.lock (atomic)
            payload = {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "task_id": task_id,
                "started_at": started_at_iso,
                "last_heartbeat": started_at_iso,
            }
            await anyio.to_thread.run_sync(
                task_store.atomic_write_json, self.lock_path, payload
            )
            self._current_task_id = task_id
            self._current_payload = payload

            # Step 4: spawn heartbeat
            self._start_heartbeat(payload)
        except BusyError:
            # Release the in-proc lock — we never took the filelock here.
            if self._in_proc_lock.locked():
                self._in_proc_lock.release()
            raise
        except Exception:
            # Best-effort rollback if any later step blows up.
            await self._release_all_locks()
            raise

    async def release(self) -> None:
        """Release both locks in REVERSE order. Idempotent for try/finally."""
        await self._stop_heartbeat()
        try:
            self.lock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            log.exception("Failed to unlink tasks/.lock during release")

        await self._release_all_locks()
        self._current_task_id = None
        self._current_payload = None

    async def _release_all_locks(self) -> None:
        if self._file_lock.is_locked:
            try:
                await self._file_lock.release()
            except Exception:
                log.exception("AsyncFileLock release raised")
        if self._in_proc_lock.locked():
            self._in_proc_lock.release()

    def _read_current_payload(self) -> tuple[str, str]:
        """Best-effort read of tasks/.lock to assemble the BUSY response."""
        if self._current_payload:
            return (
                self._current_payload.get("task_id", "unknown"),
                self._current_payload.get("started_at", ""),
            )
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
            return (
                payload.get("task_id", "unknown"),
                payload.get("started_at", ""),
            )
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return (self._current_task_id or "unknown", "")

    # ---------------------------------------------------------------------
    # Heartbeat
    # ---------------------------------------------------------------------
    def _start_heartbeat(self, payload: dict) -> None:
        """Spawn a background coroutine that refreshes ``last_heartbeat`` every 5s.

        Per §A2: ``asyncio.create_task`` + explicit cancel-on-release is the
        verified pattern. Failures inside the heartbeat (e.g. disk full) are
        logged WARNING and NEVER cancel the main task (Pitfall 7).
        """
        async def _hb() -> None:
            while True:
                try:
                    await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    raise
                try:
                    payload["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
                    await anyio.to_thread.run_sync(
                        task_store.atomic_write_json, self.lock_path, payload
                    )
                except Exception:  # noqa: BLE001 — best-effort heartbeat
                    log.warning("Heartbeat write failed; continuing", exc_info=True)

        self._heartbeat_task = asyncio.create_task(_hb(), name="lock-heartbeat")

    async def _stop_heartbeat(self) -> None:
        task = self._heartbeat_task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._heartbeat_task = None

    async def shutdown(self) -> None:
        """Called from the lifespan teardown — stops heartbeat only.

        The actual lock state is owned by ``task_manager.create``'s
        try/finally; this is a safety net, not the primary release path.
        """
        await self._stop_heartbeat()
