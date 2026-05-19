# finance-skills-mcp — OpenSpec Project Overview

## Project

**Name:** finance-skills-mcp

**Core value:** Turn any markdown skill discovered under `skills/` into a programmatically callable MCP service, with no hardcoded skill names. The specification (this OpenSpec workflow) and the message-sequence diagrams (PlantUML) are fixed before any implementation code is written — Phase 0 is the gating phase that produces the reviewable wire contract.

## Capabilities

This OpenSpec workspace declares wire contracts for the four MCP tools the server exposes:

- `specs/list-skills/` — `list_skills` tool: return the frozen in-memory catalog of available skills.
- `specs/create-task/` — `create_task` tool: accept `{prompt, skills[]}`, acquire the single-task lock, run the agent, return `task_id` (or a structured `BUSY` error when another task is in flight).
- `specs/get-task-status/` — `get_task_status` tool: read-only status poll returning one of `{working, completed, failed}` plus elapsed time.
- `specs/get-task-result/` — `get_task_result` tool: terminal-state read of `output.md` + metadata; returns an error result (not a wait) when the task is still working.

Capability folder names are **kebab-case** per OpenSpec convention; the corresponding tool names in code remain `snake_case` — different identifier spaces.

## Conventions

- Each `specs/<capability>/spec.md` follows the layout: `## Purpose`, `## Requirements` (with `### Requirement:` blocks each carrying one or more `#### Scenario:` blocks — **4 hashtags** on Scenario headings, not 3), `## Errors` table, `## Schemas` (embedded JSON Schemas under H3 headings), `## Examples` (embedded JSON request/response pairs under H3 headings).
- `## Schemas` and `## Examples` are extra sections beyond the OpenSpec-validated Requirement/Scenario core; they exist to feed the contract test (SPEC-09) that parses every embedded JSON block.
- The status vocabulary is `{working, completed, failed}` — explicitly NOT `pending` / `running` (research correction C-01).
- `outputSchema` is **never** declared on any tool spec (Claude Code bug #25081 silently drops tools that declare it from `tools/list`).
