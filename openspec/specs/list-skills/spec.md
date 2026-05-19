# list-skills Specification

## Purpose

The MCP server's `list_skills` tool returns the frozen in-memory catalog of all discoverable
skills under `skills/`. The catalog is built once at server initialization (see INIT-01..04)
and never re-read at call time — `list_skills` performs no disk I/O. The tool is registered
with the MCP `readOnlyHint: true` annotation so clients can cache its output. This trial spec
exists to empirically validate Assumption A5: does `openspec validate --all` accept extra
`## Schemas` / `## Examples` sections alongside the formal `## Requirements` block?

## Requirements

### Requirement: Catalog enumeration
The server SHALL return every entry from the in-memory catalog on every `list_skills` call.

#### Scenario: Returns all catalog entries
- GIVEN the in-memory catalog contains one or more skills
- WHEN a client calls `list_skills`
- THEN the server returns `{skills: [...]}` containing every catalog entry
- AND no disk I/O is performed at call time

## Errors

| Code | When | Recovery |
|------|------|----------|
| _(none)_ | `list_skills` declares no error codes — it is a pure read of in-memory state. | — |

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

### Happy path

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
      }
    ]
  }
}
```
