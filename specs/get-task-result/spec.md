# get-task-result Specification

## Purpose

The MCP server's `get_task_result` tool returns the terminal-state output of a previously
completed (or failed) task — the contents of `tasks/<task_id>/output.md` plus structured
metadata read from `tasks/<task_id>/status.json`. For tasks in a non-terminal state
(`working`), the tool returns a structured error result; it explicitly does NOT block, wait,
or stream — clients are expected to poll `get_task_status` first and only call
`get_task_result` once the task has reached a terminal state. This is a deliberate divergence
from the MCP 2025-11-25 Tasks experimental spec (research correction; codified in D-14).

> **No structured-output schema field is declared on this tool.** Claude Code bug #25081
> silently drops tools that declare a structured-output schema from `tools/list`. The result
> shape is documented here in the spec (and enforced by the SPEC-09 contract test in PLAN 04),
> but it is NOT exported as MCP structured-output metadata. This is decision D-11; the
> forbidden field name is the camel-case structured-output declaration referenced in MCP
> issue 25081.

## Requirements

### Requirement: Terminal-state result return
For tasks whose `status.json` records `completed` or `failed`, the server SHALL return
the full `output.md` content plus structured metadata in a success result.

#### Scenario: Completed task returns output
- GIVEN `tasks/<task_id>/status.json` records the task as `completed`
- AND `tasks/<task_id>/output.md` contains the agent's final response
- WHEN a client calls `get_task_result` with that `task_id`
- THEN the server returns `{output_markdown: <content of output.md>, metadata: {...}, status: "completed"}`
- AND `output_markdown` is the verbatim file contents

#### Scenario: Failed task returns error trace
- GIVEN `tasks/<task_id>/status.json` records the task as `failed` with an `error` field
- WHEN a client calls `get_task_result` with that `task_id`
- THEN the server returns `{output_markdown: <whatever was persisted>, metadata: {error: <error trace>, ...}, status: "failed"}`
- AND the `metadata.error` field is the trace captured at agent-failure time

### Requirement: Non-terminal error contract
For tasks in a non-terminal state (i.e., still `working`), the server MUST return an error
result — NOT a wait, NOT a block, NOT a stream of partial output. This is the deliberate
divergence from MCP 2025-11-25 Tasks (D-14).

#### Scenario: Working task returns error result
- GIVEN `tasks/<task_id>/status.json` records the task as `working` (the agent has not yet reached a terminal state)
- WHEN a client calls `get_task_result` with that `task_id`
- THEN the server returns a `CallToolResult` with `isError: true`
- AND the result carries `error_code: "TASK_STILL_WORKING"`
- AND the server does NOT block waiting for the task to reach a terminal state

### Requirement: Unknown task ID handling
The server MUST return a structured error result when the requested `task_id` does not
correspond to a directory under `tasks/` — identical contract to `get_task_status`.

#### Scenario: Unknown task_id
- GIVEN no `tasks/<task_id>/` directory exists on disk for the caller-supplied UUID
- WHEN a client calls `get_task_result` with that `task_id`
- THEN the server returns an error result with `error_code: "UNKNOWN_TASK"`

## Errors

| Code | When | Recovery |
|------|------|----------|
| `TASK_STILL_WORKING` | Task is in a non-terminal state (`working`). The server returns an error rather than blocking. | Poll `get_task_status` until it reports `completed` or `failed`, then retry. |
| `UNKNOWN_TASK` | `task_id` does not correspond to a `tasks/<id>/` directory on disk (same contract as `get_task_status`). | Caller should verify the `task_id` returned by `create_task` was passed verbatim. |

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
    "output_markdown": {"type": "string"},
    "metadata": {"type": "object"},
    "status": {"type": "string", "enum": ["completed", "failed"]}
  },
  "required": ["output_markdown", "metadata", "status"]
}
```

### Response (still-working error)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "isError": {"const": true},
    "content": {"type": "array"},
    "error_code": {"const": "TASK_STILL_WORKING"}
  },
  "required": ["isError", "error_code"]
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

### Completed result

```json
{
  "request": {"task_id": "01234567-89ab-cdef-0123-456789abcdef"},
  "response": {
    "output_markdown": "# Analysis\n\nQ3 revenue grew 12% YoY...",
    "metadata": {
      "started_at": "2026-05-19T14:30:00Z",
      "completed_at": "2026-05-19T14:30:47Z",
      "skills_used": ["fixture-skill-alpha"]
    },
    "status": "completed"
  }
}
```

### Failed result

```json
{
  "request": {"task_id": "01234567-89ab-cdef-0123-456789abcdef"},
  "response": {
    "output_markdown": "Partial output before agent crashed",
    "metadata": {
      "started_at": "2026-05-19T14:30:00Z",
      "failed_at": "2026-05-19T14:30:08Z",
      "error": "AgentSDKError: rate limit exceeded"
    },
    "status": "failed"
  }
}
```

### Still working error

```json
{
  "request": {"task_id": "01234567-89ab-cdef-0123-456789abcdef"},
  "response": {
    "isError": true,
    "content": [{"type": "text", "text": "task still working"}],
    "error_code": "TASK_STILL_WORKING"
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
