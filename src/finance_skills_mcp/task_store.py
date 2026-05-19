"""Filesystem helpers: atomic writes, path validation, task-dir creation.

All write helpers follow the tmp → fsync(fd) → os.replace → fsync(dirfd)
sequence (RESEARCH.md Pattern 3 / D-04). Callers wrap blocking helpers in
``anyio.to_thread.run_sync(...)`` from inside ``async def @mcp.tool``
bodies (D-22 / EXEC-07).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from finance_skills_mcp.ids import TASK_ID_RE

# Re-export for backwards compatibility with callers that previously
# imported TASK_ID_RE from task_store directly.
__all__ = [
    "TASK_ID_RE",
    "validate_task_id",
    "task_dir",
    "atomic_write_bytes",
    "atomic_write_text",
    "atomic_write_json",
    "create_task_dirs",
    "read_status_json",
]


def validate_task_id(task_id: str) -> None:
    """Reject anything not matching the D-05 regex. Kills path-traversal class.

    Raises:
        ValueError: if the task_id contains any character outside the
            ``YYYYMMDDTHHMMSS-<8 hex>`` shape.
    """
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        raise ValueError(f"Invalid task_id format: {task_id!r}")


def task_dir(tasks_root: Path, task_id: str) -> Path:
    """Resolve and validate the task directory path.

    Defense-in-depth path traversal guard (D-05): regex match + ``resolve``
    + ``is_relative_to``. ``tasks_root`` must already exist (``strict=True``).
    """
    validate_task_id(task_id)
    tasks_root_resolved = Path(tasks_root).resolve(strict=True)
    candidate = (tasks_root_resolved / task_id).resolve()
    if not candidate.is_relative_to(tasks_root_resolved):
        raise ValueError(f"task_id resolves outside tasks_root: {task_id!r}")
    return candidate


def atomic_write_bytes(dest: Path, data: bytes) -> None:
    """Atomic write: tmp (same dir) → fsync(fd) → os.replace → fsync(dirfd).

    The temporary file lives in the same parent directory as ``dest`` so
    ``os.replace`` is guaranteed atomic (same filesystem). ``fsync(dirfd)``
    ensures the rename itself is durable on POSIX. The dirfd fsync is guarded
    behind ``O_DIRECTORY`` availability so it is a no-op on platforms that
    do not support it (Phase 1 declines Windows explicitly per D-09).
    """
    dest = Path(dest)
    dest_dir = dest.parent
    dest_dir.mkdir(parents=True, exist_ok=True)

    fd, tmp_path_str = tempfile.mkstemp(prefix=f".{dest.name}.", dir=str(dest_dir))
    tmp_path: str | None = tmp_path_str
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest)
        tmp_path = None  # ownership transferred to os.replace
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass

    # Fsync the parent directory so the rename is durable (POSIX).
    if hasattr(os, "O_DIRECTORY"):
        try:
            dir_fd = os.open(str(dest_dir), os.O_RDONLY | os.O_DIRECTORY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


def atomic_write_text(dest: Path, text: str) -> None:
    """Atomic write of a UTF-8 text payload."""
    atomic_write_bytes(dest, text.encode("utf-8"))


def atomic_write_json(dest: Path, obj: object) -> None:
    """Atomic write of a JSON payload (indented, sorted keys for determinism)."""
    atomic_write_bytes(
        dest,
        json.dumps(obj, indent=2, sort_keys=True).encode("utf-8"),
    )


def create_task_dirs(tasks_root: Path, task_id: str) -> Path:
    """``mkdir tasks/<task_id>/{workspace,logs}``. Returns the task directory.

    ``tasks_root`` must exist before calling. ``logs/`` is created empty as a
    Phase 3 hook (D-25/26).
    """
    d = task_dir(tasks_root, task_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "workspace").mkdir(parents=True, exist_ok=True)
    (d / "logs").mkdir(parents=True, exist_ok=True)
    return d


def read_status_json(tasks_root: Path, task_id: str) -> dict:
    """Sync read of ``status.json``. Callers wrap in ``anyio.to_thread.run_sync``.

    Raises:
        FileNotFoundError: when the task directory or status file is missing.
    """
    d = task_dir(tasks_root, task_id)
    status_path = d / "status.json"
    if not status_path.is_file():
        raise FileNotFoundError(status_path)
    return json.loads(status_path.read_text(encoding="utf-8"))
