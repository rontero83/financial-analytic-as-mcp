"""Phase 2 integration in-memory test fixtures.

The autouse ``_snapshot_repo_skills_index`` fixture handles M-4 (from
02-PLAN-CHECK.md): the SC1..SC5 acceptance suite drives ``app_lifespan``
which computes ``repo_root`` from ``__file__`` and writes
``.skills-index/{catalog,errors}.json`` into the real repo root. Without
isolation, every integration test would clobber the dev-local
``.skills-index/`` contents. The fixture snapshots before each test and
restores after, so the post-suite git status is unchanged from the
pre-suite git status. ``.skills-index/`` is gitignored regardless, but
this keeps operator-inspectable state stable across test runs.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX_DIR = _REPO_ROOT / ".skills-index"


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
