# create-task Specification

## Purpose

The MCP server's `create_task` tool accepts a `prompt` plus a list of `skills` names from
the in-memory catalog, validates the request, acquires the single-task lock (the server is
explicitly single-task synchronous — REQUIREMENTS.md EXEC-05 / MCP-05), persists `input.md`
under `tasks/<task_id>/`, runs the agent via the Claude Agent SDK with the requested skills
loaded, and returns `{task_id}` once the agent terminates. While a task is in flight, every
subsequent `create_task` call returns a structured `BUSY` error carrying the in-flight
`task_id` and its `started_at` timestamp in `_meta` — clients are expected to poll
`get_task_status` against that ID rather than retry blindly. The BUSY shape is the contract's
most distinctive feature and is encoded both as a normative requirement scenario (immediately
adjacent to the happy path per D-13) and as a JSON Schema under `## Schemas`.

## Requirements

### Requirement: Synchronous task acceptance
The server SHALL accept exactly one `create_task` invocation at a time. While a task is in
flight, subsequent `create_task` calls SHALL return a structured BUSY error result (NOT a
JSON-RPC error code) so the client can recover by polling `get_task_status` on the in-flight
task id.

#### Scenario: First call succeeds
- GIVEN the server is idle (no task in flight, the single-task lock is free)
- WHEN a client calls `create_task` with a valid `prompt` and `skills` array
- THEN the server acquires the lock, persists `input.md`, runs the agent, and returns `{task_id: <uuid>}` once the agent terminates
- AND `tasks/<task_id>/status.json` ends in `completed`
- AND the single-task lock is released

#### Scenario: Second call returns BUSY
- GIVEN a task is already in flight (the lock is held by an earlier `create_task`)
- WHEN a second client calls `create_task` with any payload
- THEN the server returns a `CallToolResult` with `isError: true`
- AND the result's `_meta` object contains `inflight_task_id` (the UUID of the in-flight task) and `started_at` (ISO 8601 timestamp of when that task began)
- AND no new task directory is created
- AND the in-flight task continues uninterrupted

### Requirement: Skill validation
The server MUST reject `create_task` calls naming skills not present in the in-memory
catalog — there is no fallback, no fuzzy match, no implicit refresh of the catalog.

#### Scenario: Unknown skill
- GIVEN the catalog contains only `["fixture-skill-alpha"]`
- WHEN a client calls `create_task` with `skills: ["nonexistent"]`
- THEN the server returns an error result with `error_code: "UNKNOWN_SKILL"`
- AND no single-task lock is acquired
- AND no `tasks/<id>/` directory is created
- AND the catalog is not refreshed from disk

### Requirement: Prompt validation
The server MUST reject `create_task` calls whose `prompt` field is empty or exceeds 100 KB
(102400 bytes), surfacing the failure as a structured `INVALID_PROMPT` error before any lock
or disk operation runs.

#### Scenario: Empty prompt
- GIVEN any valid catalog
- WHEN a client calls `create_task` with `prompt: ""` and any non-empty `skills`
- THEN the server returns an error result with `error_code: "INVALID_PROMPT"`
- AND no lock is acquired
- AND no task directory is created

#### Scenario: Oversize prompt
- GIVEN any valid catalog
- WHEN a client calls `create_task` with a `prompt` string longer than 102400 characters
- THEN the server returns an error result with `error_code: "INVALID_PROMPT"`
- AND no lock is acquired
- AND no task directory is created

### Requirement: Storage failure handling
The server MUST surface filesystem failures (cannot create `tasks/<id>/`, cannot write
`input.md`, etc.) via the structured `STORAGE_ERROR` code — never an unhandled exception,
never a crash that leaves the lock held.

#### Scenario: Cannot create task directory
- GIVEN the underlying filesystem rejects creation of `tasks/<task_id>/` (disk full, permission denied, parent missing)
- WHEN a client calls `create_task` with otherwise-valid input
- THEN the server returns an error result with `error_code: "STORAGE_ERROR"`
- AND the single-task lock is released (or was never acquired before the directory check)
- AND no partial task state is left on disk

## Errors

| Code | When | Recovery |
|------|------|----------|
| `BUSY` | Another task is in flight (the single-task lock is held). Returned as `CallToolResult { isError: true, _meta: { inflight_task_id, started_at } }` — NOT a JSON-RPC `-32xxx` error code. | Wait, then poll `get_task_status` on `inflight_task_id` until it reaches a terminal state. |
| `UNKNOWN_SKILL` | A name in the request's `skills` array is not in the in-memory catalog. | Call `list_skills` to obtain the canonical catalog, then retry with valid names. |
| `INVALID_PROMPT` | `prompt` is empty (`""`) or longer than 102400 bytes (100 KB). | Trim or split the prompt and retry. |
| `STORAGE_ERROR` | The server cannot create `tasks/<task_id>/` or write `input.md` (disk full, permission denied, FS error). | Free disk space, fix permissions, or restart the server; then retry. |

## Schemas

### Request

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "prompt": {"type": "string", "minLength": 1, "maxLength": 102400},
    "skills": {
      "type": "array",
      "items": {"type": "string"},
      "minItems": 1
    }
  },
  "required": ["prompt", "skills"]
}
```

### Response (success)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "task_id": {"type": "string", "pattern": "^[0-9a-f-]{36}$"}
  },
  "required": ["task_id"]
}
```

### Response (BUSY error)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "isError": {"const": true},
    "content": {"type": "array"},
    "_meta": {
      "type": "object",
      "properties": {
        "inflight_task_id": {"type": "string"},
        "started_at": {"type": "string", "format": "date-time"}
      },
      "required": ["inflight_task_id", "started_at"]
    }
  },
  "required": ["isError", "_meta"]
}
```

### Response (validation error)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "isError": {"const": true},
    "content": {"type": "array"},
    "error_code": {
      "type": "string",
      "enum": ["UNKNOWN_SKILL", "INVALID_PROMPT", "STORAGE_ERROR"]
    }
  },
  "required": ["isError", "error_code"]
}
```

## Examples

### Happy path

```json
{
  "request": {
    "prompt": "Analyze Q3 financials",
    "skills": ["fixture-skill-alpha"]
  },
  "response": {
    "task_id": "01234567-89ab-cdef-0123-456789abcdef"
  }
}
```

### BUSY error

```json
{
  "request": {
    "prompt": "Analyze Q4",
    "skills": ["fixture-skill-alpha"]
  },
  "response": {
    "isError": true,
    "content": [{"type": "text", "text": "Task already in flight"}],
    "_meta": {
      "inflight_task_id": "01234567-89ab-cdef-0123-456789abcdef",
      "started_at": "2026-05-19T14:30:00Z"
    }
  }
}
```

### Unknown skill error

```json
{
  "request": {
    "prompt": "Analyze",
    "skills": ["nonexistent"]
  },
  "response": {
    "isError": true,
    "content": [{"type": "text", "text": "Unknown skill: nonexistent"}],
    "error_code": "UNKNOWN_SKILL"
  }
}
```
