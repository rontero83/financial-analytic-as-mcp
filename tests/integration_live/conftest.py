"""Live integration tier: skipped unless Anthropic credentials are present.

Mirrors the auth check in ``server._auth_smoke_test`` (D-12): accepts EITHER
``ANTHROPIC_API_KEY`` OR ``CLAUDE_CODE_OAUTH_TOKEN``. The Claude Agent SDK
itself accepts both auth paths, so the live tier should too — otherwise
developers logged into Claude Code via OAuth (no raw API key) cannot run the
walking skeleton locally.
"""
from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    """Skip live-marked tests unless an Anthropic credential is available."""
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        return
    skip_live = pytest.mark.skip(
        reason="neither ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN set - live tier disabled"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
