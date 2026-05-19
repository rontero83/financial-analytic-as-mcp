# list-skills Specification

## Purpose

The MCP server's `list_skills` tool returns the frozen in-memory catalog of all discoverable
skills under `skills/`. The catalog is built once at server initialization (INIT-01..04 in
REQUIREMENTS.md) by scanning `skills/<name>/SKILL.md`; runtime calls to `list_skills` read
from the in-memory catalog dataclass and perform NO disk I/O. The tool is registered with
the MCP `readOnlyHint: true` tool annotation so MCP clients (and Claude Code) know they can
cache the result and that calling it has no side effects. This is the canonical mechanism
by which a client discovers what skills it can pass to `create_task`; no skill name is ever
hardcoded on either side of the wire.

## Diagram

The sequence diagram for this capability lives next to this spec — they describe the same
contract from two angles and MUST be updated in the same commit.

- Rendered: [`list_skills.svg`](list_skills.svg)
- Source:   [`list_skills.puml`](list_skills.puml)

## Requirements

### Requirement: Catalog enumeration
The server SHALL return every entry from the in-memory catalog on every `list_skills` call.
No filtering, no pagination, no hidden entries — the response is the complete catalog as it
was loaded at init time.

#### Scenario: Empty catalog
- GIVEN the in-memory catalog is empty (no `SKILL.md` files were discovered at init time)
- WHEN a client calls `list_skills`
- THEN the server returns `{skills: []}` — an empty array, NOT an error
- AND no disk I/O is performed at call time

#### Scenario: Multiple skills
- GIVEN the in-memory catalog contains two or more skills
- WHEN a client calls `list_skills`
- THEN the server returns `{skills: [...]}` containing every catalog entry
- AND the ordering is stable across calls within one server lifetime
- AND no skill present in the catalog is omitted from the response

### Requirement: Entry shape
Each catalog entry returned by `list_skills` MUST contain the four required fields `id`,
`name`, `description`, and `path` — derived from the skill's `SKILL.md` frontmatter and
its on-disk directory (per MCP-01 + INIT-02). Any missing field is a contract violation.

#### Scenario: Minimal entry
- GIVEN the catalog contains one skill whose `SKILL.md` carries only the minimum required frontmatter (`name`, `description`)
- WHEN a client calls `list_skills`
- THEN the response entry contains all four fields: `id`, `name`, `description`, `path`
- AND `id` equals the skill's directory name (e.g., `fixture-skill-alpha`)
- AND `path` is the relative path from the repository root to the skill directory

### Requirement: Read-only annotation
The `list_skills` tool MUST be registered with the MCP `readOnlyHint: true` tool annotation
so callers (including Claude Code's tool-use loop) can treat it as side-effect-free and
cacheable.

#### Scenario: Annotation present in tools/list
- GIVEN a client issues an MCP `tools/list` request to the server
- WHEN the response is inspected
- THEN the entry for `list_skills` carries the annotation `readOnlyHint: true`
- AND no other annotation contradicts this (no `destructiveHint`, no `openWorldHint: true`)

## Errors

| Code | When | Recovery |
|------|------|----------|
| _(none)_ | `list_skills` declares no error codes — it is a pure read of in-memory state. There is no input to validate (the request body is the empty object) and no failure mode the caller can recover from at runtime. | — |

## Schemas

### Request

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {},
  "additionalProperties": false
}
```

### Response (success)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "skills": {
      "type": "array",
      "items": {
        "type": "object",
        "additionalProperties": false,
        "properties": {
          "id": {"type": "string"},
          "name": {"type": "string"},
          "description": {"type": "string"},
          "path": {"type": "string"}
        },
        "required": ["id", "name", "description", "path"]
      }
    }
  },
  "required": ["skills"]
}
```

## Examples

### Empty catalog

```json
{
  "request": {},
  "response": {
    "skills": []
  }
}
```

### Two skills

```json
{
  "request": {},
  "response": {
    "skills": [
      {
        "id": "fixture-skill-alpha",
        "name": "fixture-skill-alpha",
        "description": "Test fixture skill",
        "path": "tests/fixtures/skills/fixture-skill-alpha"
      },
      {
        "id": "financial-analyst",
        "name": "financial-analyst",
        "description": "Financial analysis skill",
        "path": "skills/financial-analyst"
      }
    ]
  }
}
```
