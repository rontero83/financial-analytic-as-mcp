"""Test double for AgentRunner. The single seam between unit/in-memory
tests and the live Claude Agent SDK call. Production code's agent_runner
module must expose an `async def run(prompt: str, skills: list[str], cwd: Path) -> str`
function (or equivalent class) so this mock is substitutable via DI.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class MockAgentRunnerError(Exception):
    """Raised when the mock is configured to simulate an agent failure."""


@dataclass
class MockAgentRunner:
    """Deterministic stand-in for the production agent_runner.

    Usage in tests::

        runner = MockAgentRunner(canned_output="# Analysis\\nFoo: 42\\n")
        result = await runner.run(prompt="...", skills=["fixture-skill-alpha"], cwd=Path("/tmp/x"))
        assert result == "# Analysis\\nFoo: 42\\n"

    Configure to fail::

        runner = MockAgentRunner(raise_on_run=RuntimeError("simulated SDK failure"))
        with pytest.raises(MockAgentRunnerError):
            await runner.run(...)
    """

    canned_output: str = "# Mock output\n\nThis is a fake agent response.\n"
    raise_on_run: Optional[BaseException] = None
    calls: list[tuple[str, tuple[str, ...], Path]] = field(default_factory=list)

    async def run(self, prompt: str, skills: list[str], cwd: Path) -> str:
        """Run the (fake) agent. Records the call; returns canned output or raises.

        Production AgentRunner contract (Phase 1):
            ``async def run(prompt: str, skills: list[str], cwd: Path) -> str``
        """
        self.calls.append((prompt, tuple(skills), cwd))
        if self.raise_on_run is not None:
            raise MockAgentRunnerError(str(self.raise_on_run)) from self.raise_on_run
        return self.canned_output
