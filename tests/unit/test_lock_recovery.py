"""Startup recovery for ``tasks/.lock`` — 5 cases per D-08 (EXEC-05).

These tests target ``LockManager.startup_recovery()`` directly (no FastMCP
``Client`` overhead — the recovery hook runs in the lifespan BEFORE the
MCP server accepts any tool call). One ``tmp_path`` per case isolates the
``tasks_root`` so the cases are hermetic and parallel-safe.

Cases (mirroring CONTEXT.md D-08 + lock_manager.py docstring):

1. **no lock file** → no-op, no exception.
2. **malformed JSON** → WARNING logged, payload removed, no exception.
3. **dead PID on same host** (ESRCH from ``os.kill(pid, 0)``) → orphan task
   marked ``failed: server_restart``, ``output.md`` atomically written, lock
   removed.
4. **alive PID on same host** (same hostname, ``last_heartbeat`` fresh) →
   ``RuntimeError`` (fail-fast; refusing to start a second server).
5. **different hostname** → ``RuntimeError`` (cross-host lock; refusing to
   start regardless of liveness).

NOTE on placement: the plan frontmatter listed this as
``tests/integration_in_memory/test_startup_recovery.py`` but the plan body
itself describes them as direct LockManager unit tests ("These tests
unit-test LockManager directly (no MCP client overhead needed)") and the
executor critical-constraints requested ``tests/unit/test_lock_recovery.py``.
Filed under ``tests/unit/`` to honour the unit-test semantics — documented
as Rule-3 deviation in 01-03-SUMMARY.md.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from finance_skills_mcp import task_store
from finance_skills_mcp.lock_manager import LockManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


VALID_TASK_ID = "20260101T000000-deadbeef"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_lock(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _seed_orphan_task(tasks_root: Path, task_id: str = VALID_TASK_ID) -> Path:
    """Materialise an orphan ``tasks/<id>/{workspace,logs,status.json}``.

    The status.json starts in ``working`` — startup recovery should flip it
    to ``failed: server_restart`` (case 3).
    """
    d = tasks_root / task_id
    (d / "workspace").mkdir(parents=True, exist_ok=True)
    (d / "logs").mkdir(parents=True, exist_ok=True)
    task_store.atomic_write_json(
        d / "status.json",
        {
            "task_id": task_id,
            "status": "working",
            "started_at": "2026-01-01T00:00:00Z",
            "elapsed_seconds": 0.0,
        },
    )
    return d


# ---------------------------------------------------------------------------
# Case 1: no lock file
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recovery_no_lock_file(tmp_path):
    """``tasks/.lock`` absent → recovery is a clean no-op."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    mgr = LockManager(tasks_root=tasks_root)
    # No assertion needed besides "does not raise" — but check there's no
    # stray lock file either.
    await mgr.startup_recovery()
    assert not (tasks_root / ".lock").exists()


# ---------------------------------------------------------------------------
# Case 2: malformed JSON
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recovery_malformed_json(tmp_path, caplog):
    """``tasks/.lock`` contains invalid JSON → log WARNING, remove, no raise."""
    import logging

    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    lock_path = tasks_root / ".lock"
    lock_path.write_text("{not json at all", encoding="utf-8")

    mgr = LockManager(tasks_root=tasks_root)
    with caplog.at_level(logging.WARNING, logger="finance_skills_mcp.lock_manager"):
        await mgr.startup_recovery()  # must not raise

    # WARNING must mention malformation.
    warning_text = " ".join(rec.message for rec in caplog.records)
    assert "malformed" in warning_text.lower(), (
        f"expected WARNING about malformed lock; got log: {warning_text!r}"
    )
    # And the broken payload is gone.
    assert not lock_path.exists(), (
        "malformed tasks/.lock must be removed during recovery"
    )


# ---------------------------------------------------------------------------
# Case 3: dead PID on same host
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recovery_dead_pid_same_host(tmp_path):
    """Dead PID + same hostname → reconcile orphan, remove lock."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()
    _seed_orphan_task(tasks_root, VALID_TASK_ID)

    # PID 999999 — astronomically unlikely to be a live process. os.kill(.., 0)
    # will raise ProcessLookupError (ESRCH).
    payload = {
        "pid": 999999,
        "hostname": socket.gethostname(),
        "task_id": VALID_TASK_ID,
        "started_at": "2026-01-01T00:00:00Z",
        "last_heartbeat": "2026-01-01T00:00:00Z",
    }
    _write_lock(tasks_root / ".lock", payload)

    mgr = LockManager(tasks_root=tasks_root)
    await mgr.startup_recovery()  # must not raise

    # tasks/.lock removed.
    assert not (tasks_root / ".lock").exists(), (
        "stale tasks/.lock for a dead PID must be removed"
    )

    # Orphan task's status.json flipped to failed:server_restart.
    status_path = tasks_root / VALID_TASK_ID / "status.json"
    assert status_path.is_file()
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["status"] == "failed", (
        f"orphan status should be 'failed' (got {status['status']!r})"
    )
    assert status["error_reason"] == "server_restart", (
        f"error_reason should be 'server_restart' (got "
        f"{status.get('error_reason')!r})"
    )

    # output.md written for the orphan (EXEC-04 — output.md before terminal flip).
    output_path = tasks_root / VALID_TASK_ID / "output.md"
    assert output_path.is_file(), (
        "orphan output.md should be atomically written during recovery"
    )
    output_text = output_path.read_text(encoding="utf-8")
    assert "interrupted" in output_text.lower(), (
        f"output.md should explain the restart-induced interruption "
        f"(got: {output_text!r})"
    )


# ---------------------------------------------------------------------------
# Case 4: alive PID on same host (fresh heartbeat)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recovery_alive_pid_same_host_raises(tmp_path):
    """Live PID + same hostname + fresh heartbeat → fail-fast RuntimeError."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()

    payload = {
        "pid": os.getpid(),  # this very test process — guaranteed alive
        "hostname": socket.gethostname(),
        "task_id": VALID_TASK_ID,
        "started_at": _now_iso(),
        "last_heartbeat": _now_iso(),  # fresh — NOT stale
    }
    _write_lock(tasks_root / ".lock", payload)

    mgr = LockManager(tasks_root=tasks_root)
    with pytest.raises(RuntimeError) as excinfo:
        await mgr.startup_recovery()
    assert "Refusing" in str(excinfo.value) or "Server already running" in str(
        excinfo.value
    ), f"RuntimeError should refuse to start; got: {excinfo.value!r}"

    # Lock file MUST remain — we did not reconcile.
    assert (tasks_root / ".lock").exists(), (
        "Refusing-to-start path must NOT delete the lock (the live owner "
        "still depends on it)"
    )


# ---------------------------------------------------------------------------
# Case 5: different hostname
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recovery_different_hostname_raises(tmp_path):
    """Different hostname → fail-fast RuntimeError regardless of PID liveness."""
    tasks_root = tmp_path / "tasks"
    tasks_root.mkdir()

    payload = {
        "pid": 1,  # liveness is irrelevant on a different host
        "hostname": "alien-host-12345",
        "task_id": VALID_TASK_ID,
        "started_at": "2026-01-01T00:00:00Z",
        "last_heartbeat": "2026-01-01T00:00:00Z",
    }
    _write_lock(tasks_root / ".lock", payload)

    mgr = LockManager(tasks_root=tasks_root)
    with pytest.raises(RuntimeError) as excinfo:
        await mgr.startup_recovery()
    assert "Refusing" in str(excinfo.value), (
        f"cross-host RuntimeError should mention Refusing; got: {excinfo.value!r}"
    )
    assert "alien-host-12345" in str(excinfo.value), (
        f"cross-host error should name the foreign hostname; got: {excinfo.value!r}"
    )
    # Lock remains — caller must investigate manually.
    assert (tasks_root / ".lock").exists()
