"""The single seam between this server and the Claude Agent SDK (D-13, EXEC-03).

Production code calls ``run(prompt, skills, cwd)``. ``MockAgentRunner`` from
``tests/_fixtures/mock_agent_runner.py`` substitutes this for unit / in-memory
tests. The signature MUST stay identical to ``MockAgentRunner.run``::

    async def run(prompt: str, skills: list[str], cwd: Path) -> str

Per EXEC-02 / C-03 (the "fresh query per task" primitive): NO ``resume=``,
NO ``continue_conversation=True``. Each call constructs a new
``ClaudeAgentOptions`` and drains the ``async for query(...)`` loop without
breaking early (Pitfall 5).
"""
from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock


async def run(prompt: str, skills: list[str], cwd: Path) -> str:
    """Run the agent for one task and return the final assistant text.

    Args:
        prompt: User prompt to send to the agent (already validated upstream).
        skills: Skill IDs to load via the SDK's ``plugins=[{"type":"local",...}]``
            mechanism. The SDK discovers ``.claude-plugin/plugin.json`` under
            ``repo_root`` and loads matching SKILL.md files.
        cwd: Workspace directory handed to the agent as its ``cwd``. Per-task
            isolation lives here (D-04).

    Returns:
        The concatenated assistant text (joined by newlines). If the agent
        produced no text, returns an empty string.
    """
    # repo_root = <this file>/../.. = src/finance_skills_mcp/../.. = repo
    repo_root = Path(__file__).resolve().parents[2]
    options = ClaudeAgentOptions(
        cwd=str(cwd),
        plugins=[{"type": "local", "path": str(repo_root)}],
        skills=list(skills),
        max_turns=20,
        permission_mode="acceptEdits",
    )

    parts: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
        elif isinstance(message, ResultMessage):
            # ResultMessage marks end-of-stream; text was already harvested
            # from AssistantMessage above. Do NOT break — drain the generator
            # to let the SDK clean up its subprocess (Pitfall 5).
            pass

    return "\n".join(parts)
