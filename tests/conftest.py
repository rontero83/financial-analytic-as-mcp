"""Pytest top-level configuration: anyio backend, shared path constants."""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_SKILLS_DIR = REPO_ROOT / "tests" / "fixtures" / "skills"


@pytest.fixture
def anyio_backend() -> str:
    """Force anyio plugin to use asyncio only (matches FastMCP runtime; D-04)."""
    return "asyncio"
