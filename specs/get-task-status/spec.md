# get-task-status Specification

## Purpose

The MCP server's `get_task_status` tool is a read-only status poll for a previously-created
task. It reads `tasks/<task_id>/status.json` from disk (a small JSON file), does NOT acquire
the single-task lock, does NOT call the Claude Agent SDK, and is designed to return in under
200 ms so it is safe to call repeatedly from a polling client. The response surfaces one of
the three canonical status values — `working`, `completed`, `failed` — that the rest of the
contract treats as the single source of truth for task lifecycle state. The vocabulary is
deliberately aligned with MCP 2025-11-25's Tasks experimental spec (research correction
C-01); see `.planning/PROJECT.md` and the DECISION-LOG for the C-01 history. Only the three
enum values declared in `## Schemas` are valid; any other value is a contract violation.

## Diagram

The sequence diagram for this capability lives next to this spec — they describe the same
contract from two angles and MUST be updated in the same commit.

- Rendered: [`get_task_status.svg`](get_task_status.svg)
- Source:   [`get_task_status.puml`](get_task_status.puml)

## Requirements

### Requirement: Status returns canonical vocabulary
The response SHALL contain a `status` field whose value is one of `"working"`,
`"completed"`, or `"failed"` — exactly the three-value enum, no other values. The enum
declared in `## Schemas` below is the single source of truth for the rest of the contract
(`get_task_result` and the contract test in PLAN 04 both reference it).

#### Scenario: Working task
- GIVEN `tasks/<task_id>/status.json` records the task as `working` (the agent has not yet reached a terminal state)
- WHEN a client calls `get_task_status` with that `task_id`
- THEN the server returns `{status: "working", elapsed_seconds: <number>, task_id: <task_id>}`
- AND `elapsed_seconds` is a non-negative number (seconds since the task's `started_at`)

#### Scenario: Completed task
- GIVEN `tasks/<task_id>/status.json` records the task as `completed` (the agent terminated successfully)
- WHEN a client calls `get_task_status` with that `task_id`
- THEN the server returns `{status: "completed", elapsed_seconds: <number>, task_id: <task_id>}`

#### Scenario: Failed task
- GIVEN `tasks/<task_id>/status.json` records the task as `failed` (the agent raised, or post-processing failed)
- WHEN a client calls `get_task_status` with that `task_id`
- THEN the server returns `{status: "failed", elapsed_seconds: <number>, task_id: <task_id>}`

### Requirement: Unknown task ID handling
The server MUST return a structured error result — never a 200-with-empty-body — when the
requested `task_id` does not correspond to a directory under `tasks/`.

#### Scenario: Unknown task_id
- GIVEN no `tasks/<task_id>/` directory exists on disk for the caller-supplied UUID
- WHEN a client calls `get_task_status` with that `task_id`
- THEN the server returns an error result with `error_code: "UNKNOWN_TASK"`
- AND the response is NOT a success envelope with an empty body

## Errors

| Code | When | Recovery |
|------|------|----------|
| `UNKNOWN_TASK` | `task_id` does not correspond to a `tasks/<id>/` directory on disk. | Caller should verify the `task_id` returned by `create_task` was passed verbatim. |

## Schemas

### Request

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "task_id": {"type": "string"}
  },
  "required": ["task_id"]
}
```

### Response (success)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "status": {"type": "string", "enum": ["working", "completed", "failed"]},
    "elapsed_seconds": {"type": "number", "minimum": 0},
    "task_id": {"type": "string"}
  },
  "required": ["status", "elapsed_seconds", "task_id"]
}
```

### Response (unknown task error)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "isError": {"const": true},
    "content": {"type": "array"},
    "error_code": {"const": "UNKNOWN_TASK"}
  },
  "required": ["isError", "error_code"]
}
```

## Examples

### Working

```json
{
  "request": {"task_id": "01234567-89ab-cdef-0123-456789abcdef"},
  "response": {
    "status": "working",
    "elapsed_seconds": 12.5,
    "task_id": "01234567-89ab-cdef-0123-456789abcdef"
  }
}
```

### Completed

```json
{
  "request": {"task_id": "01234567-89ab-cdef-0123-456789abcdef"},
  "response": {
    "status": "completed",
    "elapsed_seconds": 47.2,
    "task_id": "01234567-89ab-cdef-0123-456789abcdef"
  }
}
```

### Failed

```json
{
  "request": {"task_id": "01234567-89ab-cdef-0123-456789abcdef"},
  "response": {
    "status": "failed",
    "elapsed_seconds": 8.1,
    "task_id": "01234567-89ab-cdef-0123-456789abcdef"
  }
}
```

### Unknown task error

```json
{
  "request": {"task_id": "deadbeef-dead-beef-dead-beefdeadbeef"},
  "response": {
    "isError": true,
    "content": [{"type": "text", "text": "Unknown task_id"}],
    "error_code": "UNKNOWN_TASK"
  }
}
```
