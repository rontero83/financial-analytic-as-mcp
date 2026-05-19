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
    "stage_skills_in_workspace",
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


def stage_skills_in_workspace(
    workspace: Path,
    repo_root: Path,
    skill_entries: list[tuple[str, str]],
    skill_roots: tuple[Path, ...] | None = None,
) -> None:
    """Materialise requested skills under ``<workspace>/.claude/skills/<name>/``.

    The Claude Agent SDK discovers project skills under one of a fixed set of
    paths relative to its ``cwd`` — ``.claude/skills/`` is the canonical one.
    Passing ``skills=[...]`` to ``ClaudeAgentOptions`` is a context **filter**,
    not a discovery mechanism: the SDK won't find a skill that isn't physically
    present under one of those paths.

    Phase 1 walking-skeleton discovery: copy each skill directory's tree
    (SKILL.md plus any sibling files) into ``<workspace>/.claude/skills/<name>/``
    so the per-task workspace contains exactly the skills the client requested.
    Per-task isolation is preserved (each workspace is fresh; nothing leaks
    across tasks — that's EXEC-02's guarantee in disk form).

    Phase 2 / M-5 of 02-01: ``Skill.path`` is stored relative to the SCAN
    ROOT that produced the entry (not relative to ``repo_root``). The
    resolver tries each ``skill_roots`` entry in order — the first one
    under which ``(root / skill_path_str)`` resolves to an existing dir
    wins. If ``skill_roots`` is None, the legacy Phase-1 resolution
    (relative to ``repo_root``) is used.

    Args:
        workspace: the per-task workspace (``ClaudeAgentOptions.cwd``).
        repo_root: the project repository root; used as the legacy fallback
            scan-root when ``skill_roots`` is None.
        skill_entries: ``(skill_name, skill_path)`` tuples sourced from the
            Catalog. ``skill_path`` is the ``Skill.path`` string.
        skill_roots: tuple of absolute scan-roots (``FSMC_SKILL_ROOTS``
            entries already resolved). When set, each ``skill_path`` is
            resolved against each root in order until a hit is found.

    Notes:
        Symlinks would be lighter but risk breaking sandboxed agent reads
        when ``permission_mode`` evolves; a flat directory copy is robust
        and cheap (the fixture skill is one file).
    """
    import shutil

    if not skill_entries:
        return
    workspace = Path(workspace)
    repo_root = Path(repo_root)
    # If no scan-roots supplied (legacy callers / Phase-1 tests that still
    # pass a Skill.path that is repo_root-relative), fall back to repo_root.
    candidate_roots: tuple[Path, ...] = (
        tuple(Path(r) for r in skill_roots) if skill_roots else (repo_root,)
    )
    target_root = workspace / ".claude" / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    for skill_name, skill_path_str in skill_entries:
        # Try every scan-root in order. Phase-2 indexer paths are typically
        # ``"<skill-name>"`` (one level under the scan-root); Phase-1 legacy
        # paths were ``"tests/fixtures/skills/<skill-name>"`` relative to
        # repo_root — both resolve cleanly via this loop.
        source: Path | None = None
        attempted: list[Path] = []
        for root in candidate_roots:
            candidate = (Path(root) / skill_path_str).resolve()
            attempted.append(candidate)
            if candidate.is_dir():
                source = candidate
                break
        if source is None:
            # No scan-root contained the skill — surface as a hard error so
            # the caller turns it into STORAGE_ERROR rather than letting the
            # SDK silently fail to find the skill.
            raise FileNotFoundError(
                f"Skill {skill_name!r} path does not exist on disk; "
                f"tried: {[str(p) for p in attempted]}"
            )
        dest = target_root / skill_name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest, symlinks=False)


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
