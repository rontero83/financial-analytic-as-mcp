"""Live integration tier: skipped unless ANTHROPIC_API_KEY is set."""
from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip live-marked tests unless ANTHROPIC_API_KEY is set."""
    if os.getenv("ANTHROPIC_API_KEY"):
        return
    skip_live = pytest.mark.skip(reason="ANTHROPIC_API_KEY unset - live tier disabled")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
