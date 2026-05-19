"""Task orchestration (D-13).

**Task 2a placeholder.** This module exists so ``server.py``'s lifespan
shell can construct a ``TaskManager`` instance without an ImportError.
Task 2b of plan 01-01 replaces these stubs with the full validate → lock →
write → run → persist → release orchestration per RESEARCH.md
§task_manager.

The class API surface defined here is the contract Task 2b implements
behind. Methods raise ``NotImplementedError`` to keep accidental calls
loud, not silent.
"""
from __future__ import annotations

from pathlib import Path


class TaskManager:
    """Placeholder for Task 2b. See module docstring."""

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
        self.agent_runner = agent_runner_module
        self.task_store = task_store_module

    async def create(self, prompt: str, skills: list[str]):
        raise NotImplementedError("TaskManager.create — wired in Task 2b")

    async def get_status(self, task_id: str):
        raise NotImplementedError("TaskManager.get_status — wired in Task 2b")

    async def get_result(self, task_id: str):
        raise NotImplementedError("TaskManager.get_result — wired in Task 2b")
