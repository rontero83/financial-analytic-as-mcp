# finance-skills-mcp — OpenSpec Project Overview

## Project

**Name:** finance-skills-mcp

**Core value:** Turn any markdown skill discovered under `skills/` into a programmatically callable MCP service, with no hardcoded skill names. The specification (this OpenSpec workflow) and the message-sequence diagrams (PlantUML) are fixed before any implementation code is written — Phase 0 is the gating phase that produces the reviewable wire contract.

## Capabilities

This OpenSpec workspace declares wire contracts for the four MCP tools the server exposes (plus the server bootstrap sequence). Each capability has a `spec.md` (the wire contract) and a co-located PlantUML sequence diagram (`*.puml` source + rendered `*.svg`):

| Capability | Spec | Diagram (source) | Diagram (rendered) |
|---|---|---|---|
| Server bootstrap (not a tool) | [`init/spec.md`](init/spec.md) | [`init/init.puml`](init/init.puml) | [`init/init.svg`](init/init.svg) |
| `list_skills` tool | [`list-skills/spec.md`](list-skills/spec.md) | [`list-skills/list_skills.puml`](list-skills/list_skills.puml) | [`list-skills/list_skills.svg`](list-skills/list_skills.svg) |
| `create_task` tool | [`create-task/spec.md`](create-task/spec.md) | [`create-task/create_task.puml`](create-task/create_task.puml) | [`create-task/create_task.svg`](create-task/create_task.svg) |
| `get_task_status` tool | [`get-task-status/spec.md`](get-task-status/spec.md) | [`get-task-status/get_task_status.puml`](get-task-status/get_task_status.puml) | [`get-task-status/get_task_status.svg`](get-task-status/get_task_status.svg) |
| `get_task_result` tool | [`get-task-result/spec.md`](get-task-result/spec.md) | [`get-task-result/get_task_result.puml`](get-task-result/get_task_result.puml) | [`get-task-result/get_task_result.svg`](get-task-result/get_task_result.svg) |

**Shared diagram include:** [`_common.puml`](_common.puml) — `!include`-d by every per-capability `.puml` for shared participants/styles. Edit it before per-diagram tweaks if a change applies to all flows.

Capability folder names are **kebab-case** per OpenSpec convention; the corresponding tool names in code remain `snake_case` — different identifier spaces.

## Conventions

- Each `specs/<capability>/spec.md` follows the layout: `## Purpose`, `## Requirements` (with `### Requirement:` blocks each carrying one or more `#### Scenario:` blocks — **4 hashtags** on Scenario headings, not 3), `## Errors` table, `## Schemas` (embedded JSON Schemas under H3 headings), `## Examples` (embedded JSON request/response pairs under H3 headings).
- `## Schemas` and `## Examples` are extra sections beyond the OpenSpec-validated Requirement/Scenario core; they exist to feed the contract test (SPEC-09) that parses every embedded JSON block.
- The status vocabulary is `{working, completed, failed}` — explicitly NOT `pending` / `running` (research correction C-01).
- `outputSchema` is **never** declared on any tool spec (Claude Code bug #25081 silently drops tools that declare it from `tools/list`).
