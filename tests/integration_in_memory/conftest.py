"""Phase 2 / Phase 3 integration in-memory test fixtures.

Two autouse fixtures cohabit here:

1. ``_snapshot_repo_skills_index`` (M-4 from 02-PLAN-CHECK.md) — the
   SC1..SC5 acceptance suite drives ``app_lifespan`` which computes
   ``repo_root`` from ``__file__`` and writes ``.skills-index/`` into the
   real repo root. Snapshot+restore preserves operator-inspectable state.

2. ``_ensure_logging_configured`` (M-2 from 03-PLAN-CHECK.md) — in-memory
   tests bypass ``server.main()`` (the Phase-3 wire site for
   ``configure_logging()``), so structlog stays unconfigured by default
   in this tier. This session-scoped autouse fixture calls
   ``configure_logging()`` exactly once per test session so per-task
   structlog log writes have a global pipeline to inherit from.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from finance_skills_mcp.logging_config import configure_logging

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX_DIR = _REPO_ROOT / ".skills-index"


@pytest.fixture(autouse=True, scope="session")
def _ensure_logging_configured():
    """Wire structlog once per test session (M-2 fix from 03-PLAN-CHECK.md).

    The in-memory FastMCP ``Client(mcp)`` exercises ``app_lifespan`` but
    NOT ``server.main()`` — and ``main()`` is the canonical wire site for
    ``configure_logging()`` after Plan 03-01 Task 3 lands. Without this
    fixture, ``TaskManager.create()``'s global stderr log calls (and
    therefore Test 7's log-injection regression assertion against the
    global pipeline) would fall through to a no-op structlog backend.

    ``configure_logging`` is idempotent — the second call from a real
    server boot during a live integration test is a safe no-op.
    """
    configure_logging()
    yield


@pytest.fixture(autouse=True)
def _snapshot_repo_skills_index(tmp_path_factory: pytest.TempPathFactory):
    """Snapshot+restore ``<repo_root>/.skills-index/`` around every test.

    M-4 mitigation choice (A): the lifespan's ``persist_index`` writes into
    the real repo root because ``repo_root = Path(__file__).resolve().parents[2]``
    is computed inside the production module — not test-controllable without
    a production seam. Tests intentionally drive the lifespan, so the
    side-effect is unavoidable. This fixture moves the pre-existing
    ``.skills-index/`` aside, yields control, and restores it.
    """
    snapshot_dir: Path | None = None
    pre_exists = _INDEX_DIR.exists()
    if pre_exists:
        snapshot_dir = tmp_path_factory.mktemp("skills_index_snapshot")
        # shutil.copytree refuses if the dst exists; mktemp creates an
        # empty dir, so target one level deeper.
        shutil.copytree(_INDEX_DIR, snapshot_dir / "snap")
    try:
        yield
    finally:
        # Wipe whatever the test produced, then restore the original (if any).
        if _INDEX_DIR.exists():
            shutil.rmtree(_INDEX_DIR)
        if snapshot_dir is not None:
            shutil.copytree(snapshot_dir / "snap", _INDEX_DIR)
