"""Single-task lock + startup recovery (D-06..D-09).

**Task 2a placeholder.** This module is created as an importable stub so
``server.py``'s lifespan shell loads without an ImportError. Task 2b of plan
01-01 replaces these stubs with the full ``asyncio.Lock`` + ``AsyncFileLock``
+ heartbeat + startup recovery implementation per RESEARCH.md §lock_manager.

The class API surface defined here is the contract Task 2b will implement
behind. Do not add behavior to these methods in Task 2a — Task 2b will
overwrite this file entirely.
"""
from __future__ import annotations

from pathlib import Path


class BusyError(Exception):
    """Raised when the lock cannot be acquired (another task in flight).

    The caller in ``task_manager.create`` converts this into the BUSY error
    wire shape per D-23.
    """

    def __init__(self, inflight_task_id: str, started_at: str):
        super().__init__(f"Task {inflight_task_id} already in flight")
        self.inflight_task_id = inflight_task_id
        self.started_at = started_at


class LockManager:
    """Placeholder for Task 2b. See module docstring.

    Methods raise NotImplementedError so that any accidental call before
    Task 2b lands is loud, not silent.
    """

    def __init__(self, tasks_root: Path):
        self.tasks_root = Path(tasks_root)

    async def startup_recovery(self) -> None:
        raise NotImplementedError("LockManager.startup_recovery — wired in Task 2b")

    async def try_acquire(self, task_id: str, started_at_iso: str) -> None:
        raise NotImplementedError("LockManager.try_acquire — wired in Task 2b")

    async def release(self) -> None:
        raise NotImplementedError("LockManager.release — wired in Task 2b")

    async def shutdown(self) -> None:
        # Shutdown must be tolerant of being called from a lifespan that
        # never actually started — used in the Task 2a smoke test path.
        return None
