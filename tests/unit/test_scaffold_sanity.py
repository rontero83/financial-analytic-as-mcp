"""Smoke test: confirms the test scaffold imports cleanly."""
from __future__ import annotations

import pytest

import finance_skills_mcp
from tests._fixtures.mock_agent_runner import MockAgentRunner, MockAgentRunnerError


def test_package_imports() -> None:
    """finance_skills_mcp package is importable (Phase 0 placeholder)."""
    assert finance_skills_mcp is not None


def test_mock_agent_runner_imports() -> None:
    """MockAgentRunner seam is importable from the test fixtures tier."""
    runner = MockAgentRunner()
    assert runner.canned_output.startswith("# Mock output")
    assert runner.calls == []
    assert issubclass(MockAgentRunnerError, Exception)


@pytest.mark.anyio
async def test_mock_agent_runner_runs() -> None:
    """MockAgentRunner.run returns the canned output and records the call."""
    from pathlib import Path

    runner = MockAgentRunner(canned_output="# Hello\n")
    result = await runner.run(prompt="test", skills=["fixture-skill-alpha"], cwd=Path("/tmp/x"))
    assert result == "# Hello\n"
    assert runner.calls == [("test", ("fixture-skill-alpha",), Path("/tmp/x"))]
