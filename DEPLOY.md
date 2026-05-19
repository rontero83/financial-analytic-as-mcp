# Deployment Guide

Operator runbook for shipping the Finance Skills MCP Server. Read
[`README.md`](./README.md) first for project overview, quickstart, and the full
environment-variable / exit-code reference.

This document complements README.md — it does not replace it.

## Preflight

Before running the server in any non-throwaway environment, walk this checklist:

1. **Confirm an auth credential is available in the process environment.**
   - Production: set `ANTHROPIC_API_KEY` to an **API-billing** key. See
     README §Auth setup for the 2026-06-15 billing-model split note (Pro/Max
     subscription tokens are accepted by the smoke test but will fail the first
     real `create_task`).
   - Local dev: `CLAUDE_CODE_OAUTH_TOKEN` from an active Claude Code session is
     acceptable for interactive use.
   - Quick check (fails non-zero if neither var is set):
     ```bash
     python -c "import os; assert os.getenv('ANTHROPIC_API_KEY') or os.getenv('CLAUDE_CODE_OAUTH_TOKEN'), 'no auth set'"
     ```

2. **Decide on the disk-space threshold.**
   - Default `FSMC_FREE_SPACE_MB=100`. Raise it on volumes shared with other
     workloads or where individual tasks may produce large `output.md` artifacts.
   - Invalid values (non-int, zero, negative) cause `sys.exit(5)` at startup —
     fix the env var before retrying.

3. **Decide on the skill scan roots.**
   - Default `FSMC_SKILL_ROOTS=skills` (relative to repo root).
   - For multi-source setups, use colon-separated paths:
     `FSMC_SKILL_ROOTS=skills:custom-skills`.
   - The nested `business-investment-advisor/skills/` tree from the legacy
     layout is **excluded by default** — do NOT add it to the scan roots, it
     would trigger a `DUPLICATE_NAME` refusal at startup (`sys.exit(3)`).

4. **Provision the `tasks/` directory writable by the process owner.**
   - The server creates `tasks/<task_id>/{workspace,logs}` under the repo root
     (or under `FSMC_REPO_ROOT` if that env var is set).
   - Verify the user running the server has write permission on this directory.
     A read-only volume here will surface as `create_task` failures, not at
     startup.

5. **Configure the nightly CI workflow secret.** (Required only if you intend to
   run `.github/workflows/nightly-live.yml` — the live-SDK integration suite
   plan 03-03 introduced.)
   - Open the GitHub repository UI -> **Settings -> Secrets and variables -> Actions**.
   - Click "New repository secret".
   - Name: `ANTHROPIC_API_KEY`. Value: an API-billing key (NOT a Pro/Max OAuth
     token — CI runs unattended and the SDK auth call cannot trigger a
     browser-based OAuth flow).
   - The workflow references this as `${{ secrets.ANTHROPIC_API_KEY }}`.
   - Do NOT commit this key to any file in the repo.

6. **Verify the CLI entry point smoke-starts.**
   - `uv run python -m finance_skills_mcp` should print the auth-check result and
     a one-line indexed-catalog summary to stderr, then wait on stdio for MCP
     requests.
   - If it exits with code 2/3/4/5 instead, see the
     [Exit-code troubleshooting](#exit-code-troubleshooting) table below.

## Runtime

The server is a **stdio-based MCP service** — it speaks MCP over stdin/stdout,
with all logs on stderr. There is no HTTP listener and no socket binding.

### Invocation

```bash
# Direct (foreground)
uv run python -m finance_skills_mcp

# Background (development)
uv run python -m finance_skills_mcp 2>server.stderr.log &

# systemd unit (production sketch — adjust paths/user/secret-store integration)
# /etc/systemd/system/finance-skills-mcp.service:
# [Unit]
# Description=Finance Skills MCP Server
# After=network.target
# [Service]
# Type=simple
# User=mcp
# WorkingDirectory=/srv/finance-skills-mcp
# EnvironmentFile=/etc/finance-skills-mcp.env   # contains ANTHROPIC_API_KEY=...
# Environment=FSMC_FREE_SPACE_MB=500
# Environment=FSMC_SKILL_ROOTS=skills
# ExecStart=/usr/local/bin/uv run python -m finance_skills_mcp
# Restart=on-failure
# [Install]
# WantedBy=multi-user.target

# launchd plist (macOS) — analogous structure with KeepAlive; load via launchctl.
```

### Log inspection

Per-task structured logs land at `tasks/<task_id>/logs/server.jsonl` (one JSON
object per line, `structlog`-formatted, introduced by plan 03-01). Every line
carries `task_id`, `tool_name`, `skill_ids`, and `event`.

Useful queries:

```bash
# All events for a specific task
cat tasks/20260520T120000-abc12345/logs/server.jsonl | jq -c .

# Only the agent-call timing across all tasks
cat tasks/*/logs/server.jsonl | jq -c 'select(.event == "agent_call_returned") | {task_id, elapsed_ms}'

# All failed tasks in the last week (filesystem mtime gate)
find tasks -name server.jsonl -mtime -7 -exec grep -l '"event":"task_failed"' {} +
```

There is **NO global server log file** in v1 (per phase decisions D-37 / D-40).
Cross-task events (lifespan transitions, auth refusal, indexer fatal exits) go
to stderr only. Redirect stderr to a file at the supervisor level if you want a
unified log.

### Retention

No automatic retention in v1. The `tasks/` directory grows unbounded.
Operator-managed cleanup:

```bash
# Delete completed tasks older than 30 days
find tasks -maxdepth 1 -type d -mtime +30 -exec rm -rf {} +
```

Schedule via cron / systemd timer as appropriate for your retention policy.

### Exit-code troubleshooting

| Code | Meaning                                                                              | First action                                                                                                                                                                            |
| ---- | ------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `2`  | No auth credential, OR `FSMC_REPO_ROOT` invalid                                       | Set `ANTHROPIC_API_KEY` (or `CLAUDE_CODE_OAUTH_TOKEN`); verify `FSMC_REPO_ROOT` (if set) points at a directory containing `src/finance_skills_mcp/`.                                     |
| `3`  | `DUPLICATE_NAME` (two `SKILL.md` files share a `name` frontmatter)                    | Read `.skills-index/errors.json` — both conflicting absolute paths are listed. Rename or delete one. (See Preflight step 3: the legacy nested tree is the most common cause.)            |
| `4`  | Empty catalog — no skills survived validation                                         | Verify `FSMC_SKILL_ROOTS` paths exist and contain valid `SKILL.md` files. Check `.skills-index/errors.json` for per-code rejection counts (missing frontmatter, malformed YAML, etc.).    |
| `5`  | Invalid `FSMC_FREE_SPACE_MB` — `sys.exit(5)` from the env parser introduced this phase | The env var must be a positive integer. Fix the value and restart. The stderr message names the offending value.                                                                       |

## Verification

Before declaring any deployment "live", walk the **"Looks Done But Isn't"
checklist** below. This list is reproduced verbatim from
[`.planning/research/PITFALLS.md`](./.planning/research/PITFALLS.md) (the source
of truth — if these two copies ever drift, that file wins; sync DEPLOY.md to it
in the same commit that edits the checklist).

### Looks Done But Isn't Checklist

Verify before declaring any phase complete.

- [ ] Add a brand-new fixture skill `aaaa-test-skill-zzzz` to `skills/`, restart server, confirm it appears in `list_skills` AND is invocable via `create_task` — no source changes (UNIV-01).
- [ ] Remove that skill, restart, confirm it's gone from `list_skills` (UNIV-02).
- [ ] CI grep finds no string match of any real skill name in production source (UNIV-03).
- [ ] During a long `create_task`, `get_task_status` polls return within 200 ms (event loop not blocked).
- [ ] After a forced server kill (`kill -9`) during a running task, restart cleans the stale lock and marks the task `failed: server_restart`.
- [ ] Two clients calling `create_task` simultaneously: exactly one succeeds, the other gets `BUSY` with the in-flight `task_id`.
- [ ] Each MCP tool's actual response validates against the OpenSpec-generated schema (contract test passes).
- [ ] PlantUML diagrams for all six entry points (init + 4 tools + 1 busy-state branch) render cleanly in CI.
- [ ] Two SKILL.md files with the same `name` cause server startup to FAIL with both paths in the error (not silent dedupe).
- [ ] A SKILL.md with no `description` is rejected on indexing; the rejection is visible via `list_skill_errors` or equivalent.
- [ ] A `create_task` exceeding configured timeout terminates the agent cleanly, releases the lock, and a subsequent `create_task` succeeds.
- [ ] Auth smoke test runs at server start; misconfigured env produces an actionable error before any MCP request is served.
- [ ] Test pyramid in place: unit tests run in < 10 s; in-memory MCP tests run without spawning Agent SDK; integration tests gated to nightly.
- [ ] Path-traversal probes (`../`, absolute paths, symlink escapes) on skill names and task_ids all rejected.
- [ ] Atomic write test: 100-iteration race between `create_task` and `get_task_status` produces zero partial JSON reads.
- [ ] Disk-full simulation: `create_task` rejects cleanly when free space below threshold.
- [ ] Two-task contamination test: Task 2 cannot recall information given only to Task 1.
- [ ] OpenSpec spec lists all error codes for each tool; every code has a test that triggers it.
- [ ] `tasks/.archive/` (or retention deletion) configured; orphan task directories from prior crashes are cleaned at startup.
- [ ] CONCERNS.md "Duplicate Nested Directory Tree" is resolved OR explicitly handled by indexer (Pitfall 6 mechanism).

### After-deploy smoke test

Following a fresh deploy, run a one-off `create_task` against a fixture skill to
verify the full pipeline (auth -> indexing -> tool invocation -> agent SDK ->
output persistence):

```bash
# Using MCP Inspector or an equivalent MCP client, call:
#   list_skills            -> expect the catalog including the fixture skill
#   create_task            -> {prompt: "echo ping", skills: ["fixture-skill-alpha"]}
#   get_task_status        -> expect "completed"
#   get_task_result        -> expect "FIXTURE-ECHO::" in output_markdown

# Then inspect the structured log for the just-completed task:
LATEST=$(ls -1dt tasks/*/ | head -1)
cat "${LATEST}logs/server.jsonl" | jq -c .
```

If any step in the after-deploy smoke test fails, consult the
[Exit-code troubleshooting](#exit-code-troubleshooting) table and the
`tasks/<task_id>/status.json` file before declaring the deployment broken.
