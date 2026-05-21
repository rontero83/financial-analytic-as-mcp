# Finance Skills MCP Server

A [FastMCP](https://github.com/jlowin/fastmcp) server that wraps the markdown-defined
finance skills in `skills/` and exposes them as MCP tools to any compatible client
(Claude Code, MCP Inspector, custom MCP clients). Skills are discovered from disk at
startup — adding a new skill is a `skills/<new>/SKILL.md` drop plus a server restart,
with **no source-code changes**.

## What is this

The core value of this project: **turn any markdown-defined skill in `skills/` into a
programmatically callable MCP service without hardcoding skill names**. Drop a new
`SKILL.md` file in `skills/` and after a server restart it shows up in `list_skills`
and is invocable via `create_task` — no Python code touched, no enum updated, no
deploy pipeline changes.

The server exposes four MCP tools (`list_skills`, `create_task`, `get_task_status`,
`get_task_result`) and runs in a **single-task synchronous** mode by design — concurrent
`create_task` calls return `BUSY` with the in-flight `task_id`. That is a deliberate
contract decision (the server is not a queue), documented in `specs/create-task/spec.md`.

The agent runtime is the [Claude Agent SDK](https://docs.anthropic.com/en/api/agent-sdk),
spawned as a subprocess per task. Per-task structured logs land at
`tasks/<task_id>/logs/server.jsonl`.

## Quickstart

Prerequisites: Python 3.10+, [`uv`](https://github.com/astral-sh/uv).

```bash
# 1. Clone and install deps
git clone <repo-url>
cd business-investment-advisor
uv sync

# 2. Set ONE auth credential (see "Auth setup" below for the 2026-06-15 caveat)
export ANTHROPIC_API_KEY=sk-ant-...   # an API-billing key, NOT a Pro/Max OAuth token

# 3. Run the server (stdio MCP — speaks MCP on stdin/stdout, logs to stderr)
uv run python -m finance_skills_mcp

# 4. (Optional) Talk to it interactively with MCP Inspector
#    https://github.com/modelcontextprotocol/inspector
```

The server prints the auth-check result and the indexed skill catalog summary to
stderr and then waits on stdio for MCP requests. There is no HTTP listener.

## MCP Contract

The wire contract is defined as OpenSpec-style markdown specs co-located with their
PlantUML sequence diagrams under `specs/`. **Source of truth:**
[`specs/README.md`](./specs/README.md).

Brief overview:

| Tool               | Purpose                                                                                                              |
| ------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `list_skills`      | Returns the in-memory catalog of indexed skills (name, description, tags).                                           |
| `create_task`      | Accepts `{prompt, skills}`, runs the Agent SDK subprocess, returns `{task_id}`. Single-task synchronous: concurrent calls return `BUSY`. |
| `get_task_status`  | Returns `{status, elapsed_seconds, task_id}` where `status ∈ {working, completed, failed}`. Non-blocking; safe to poll. |
| `get_task_result`  | Returns `{output_markdown, metadata}` for terminal (`completed` / `failed`) tasks.                                   |

See each capability's spec under `specs/<capability>/spec.md` for full request/response
JSON schemas and example payloads. Init flow (skill indexing, auth smoke test) is in
[`specs/init/spec.md`](./specs/init/spec.md).

## Auth setup

The Agent SDK subprocess this server spawns requires Anthropic credentials. Either of
these environment variables is accepted (the OPS-02 smoke test enforces presence at
server startup — the server refuses to start with `sys.exit(2)` if neither is set):

- `ANTHROPIC_API_KEY` — an **API-billing** key from
  [console.anthropic.com](https://console.anthropic.com) under your API account.
  **Recommended for production deployments and for the nightly CI workflow.**
- `CLAUDE_CODE_OAUTH_TOKEN` — a Pro/Max subscription OAuth token. Convenient for
  local development if you already have an active Claude Code session.

### The 2026-06-15 Anthropic billing-model split

Effective **2026-06-15**, Anthropic separated **API billing** from the **Pro/Max
subscription**. These are now distinct billing entities:

- An API-billing key (`ANTHROPIC_API_KEY`) bills against your API account.
- A Pro/Max OAuth token (`CLAUDE_CODE_OAUTH_TOKEN`) authorizes against your subscription.

The OPS-02 smoke test (`_auth_smoke_test` in `src/finance_skills_mcp/server.py`)
checks that **one credential is present** but does **not** validate it against the
API. If you provide a Pro/Max subscription token but the SDK subprocess cannot
authenticate API calls (because the subscription does not cover programmatic API
access in your account), the **first** `create_task` call will fail with the SDK's
authentication error surfaced in `tasks/<task_id>/status.json`.

For production deployments and for the nightly CI workflow
(`.github/workflows/nightly-live.yml`), use an **API-billing** key. Check the
[Anthropic Console](https://console.anthropic.com) for the current billing-model
details.

## Configuration

All configuration is via environment variables (no config file). Defaults are
operator-friendly; overrides are read **once at startup** (no hot-reload — restart
to apply changes).

| Variable                                          | Default          | Purpose                                                                                                                                                                       | Invalid value                                                                                                                  |
| ------------------------------------------------- | ---------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `FSMC_SKILL_ROOTS`                                | `skills`         | Colon-separated list of directories scanned for `SKILL.md` files. Relative paths resolve against repo root. Duplicates are deduplicated with a stderr warning.                | Empty / whitespace-only falls back to default. A non-existent root is reported via `IndexErrorCode.MISSING_ROOT`; other roots continue to scan. |
| `FSMC_FREE_SPACE_MB`                              | `100`            | Positive integer in canonical decimal form: `[1-9][0-9]*` after surrounding whitespace is stripped. NO sign (`+100` rejected), NO digit-grouping underscores (`1_000` rejected), NO leading zeros (`0100` rejected — would be octal-64 in shell idiom but Python 3 reads it as 100, so the parser rejects rather than choose), NO alternate base (`0x64`, `0b10`, `1e3` rejected). Disk-space threshold in megabytes; `create_task` refuses with a `DISK_FULL` error when free space on the tasks volume is below this. | Any value outside the canonical form above, or zero / negative -> `sys.exit(5)` with an actionable stderr message naming the offending value. |
| `FSM_TASK_TIMEOUT_SECONDS`                        | `600`            | Hard per-task wall-time ceiling in seconds. On timeout the task is marked `failed: timeout` and the single-task lock is released.                                              | Non-float values raise on import.                                                                                              |
| `ANTHROPIC_API_KEY` **or** `CLAUDE_CODE_OAUTH_TOKEN` | (none)        | One of these MUST be set. See "Auth setup" above.                                                                                                                              | Missing -> `sys.exit(2)`.                                                                                                      |
| `FSMC_REPO_ROOT`                                  | (auto-derived)   | Override the repo-root path when running from a wheel install. The auto-derivation walks up from the package location.                                                          | A path that does not contain `src/finance_skills_mcp/` -> `sys.exit(2)`.                                                       |

### Server exit codes

| Code | Meaning                                                                                       |
| ---- | --------------------------------------------------------------------------------------------- |
| `0`  | Clean shutdown.                                                                               |
| `2`  | No auth credentials, OR `FSMC_REPO_ROOT` does not resolve to a valid repo.                    |
| `3`  | `DUPLICATE_NAME` across scan roots (two `SKILL.md` files with the same `name` frontmatter).    |
| `4`  | Empty catalog after indexing (no valid skills survived validation).                            |
| `5`  | Invalid `FSMC_FREE_SPACE_MB` (non-canonical-decimal, zero, or negative — see Configuration table above for the canonical-form contract). Introduced by `sys.exit(5)` in plan 03-02. |

## Where to go next

- **Deploy to production:** [`DEPLOY.md`](./DEPLOY.md) — preflight checklist,
  runtime supervision (systemd / launchd sketches), the exit-code troubleshooting
  table, and the full "Looks Done But Isn't" verification list.
- **Wire contract:** [`specs/README.md`](./specs/README.md) — OpenSpec-style specs
  + PlantUML sequence diagrams for every MCP tool and the init flow.
- **AI-assisted contributor guide:** [`CLAUDE.md`](./CLAUDE.md) — project
  guidelines and stack notes for AI coding assistants.
