# init Specification

## Purpose

This spec describes the **server bootstrap sequence** that runs once when the Finance Skills
MCP server process starts. It is NOT an MCP tool — clients cannot invoke it, and it has no
wire request/response. It captures the contract between server startup code and the rest of
the system: which roots are scanned, what `SKILL.md` parsing must accept, how the catalog is
persisted, and what counts as a fatal startup error vs a warning.

The diagram is the canonical visual counterpart of this spec and is co-located on purpose.
Both this spec and `specs/list-skills/spec.md` reference the same in-memory `Catalog` object;
the difference is that this spec describes how the catalog comes into existence, and
`list-skills` describes how clients read from it.

## Diagram

The sequence diagram for this capability lives next to this spec — they describe the same
contract from two angles and MUST be updated in the same commit. The diagram shows the full
bootstrap path: SkillIndexer → frontmatter parse → catalog persistence → in-memory load →
LockManager + TaskManager construction → FastMCP ready.

- Rendered: [`init.svg`](init.svg)
- Source:   [`init.puml`](init.puml)

## Requirements

### Requirement: Skill discovery walks configured roots
At startup the server SHALL scan every configured skill root for `SKILL.md` files. The
default root is `skills/`. Additional roots MAY be added via configuration (the production
default is single-root; the multi-root configuration knob exists for UNIV-01/UNIV-02 testing
in REQUIREMENTS.md).

#### Scenario: Default single-root discovery
- GIVEN no extra skill roots are configured beyond the default `skills/`
- WHEN the server starts
- THEN the SkillIndexer recursively scans `skills/<name>/SKILL.md`
- AND every other path in the repository is ignored (no scan of `business-investment-advisor/`,
  no scan of `tests/fixtures/skills/`, no scan of `node_modules/`, no scan of `.git/`)

#### Scenario: Empty skill root
- GIVEN the configured skill root contains no `SKILL.md` files
- WHEN the server starts
- THEN the SkillIndexer logs a warning that the catalog is empty
- AND the server still completes bootstrap successfully
- AND `list_skills` will return `{skills: []}` for every subsequent call

### Requirement: SKILL.md frontmatter parsing
The SkillIndexer SHALL parse YAML frontmatter from every discovered `SKILL.md` file using
the `python-frontmatter` library. The only required frontmatter fields are `name` and
`description`. All other fields (`version`, `tags`, `scripts`, `references`) are optional
and pass through to the catalog as-is.

#### Scenario: Well-formed SKILL.md
- GIVEN a `SKILL.md` with frontmatter `name: financial-analyst` and a non-empty `description:`
- WHEN the SkillIndexer parses it
- THEN the resulting catalog entry has `id` = directory name (kebab-case, stable),
  `name` = frontmatter `name`, `description` = frontmatter `description`, and `path` =
  the relative path to the containing directory

#### Scenario: Malformed SKILL.md is skipped, not fatal
- GIVEN a `SKILL.md` with broken YAML frontmatter or a missing required field
- WHEN the SkillIndexer parses it
- THEN the file is recorded in `.skills-index/errors.json` with its path and the parse error
- AND the file is excluded from the catalog
- AND the server still completes bootstrap successfully
- AND bootstrap fails ONLY if every discovered SKILL.md is rejected (catalog would be empty
  AND at least one parse error occurred — distinguishes "no skills present" from "all
  skills broken")

### Requirement: Duplicate skill names abort startup
If two `SKILL.md` files declare the same `name` in their frontmatter, the SkillIndexer SHALL
refuse to start the server. This is a fail-loud guard against the indexer landmine flagged
in `.planning/codebase/CONCERNS.md` and in research correction C-05.

#### Scenario: Duplicate name across roots
- GIVEN `skills/foo/SKILL.md` declares `name: foo`
- AND `skills-extra/foo/SKILL.md` (a second configured root) also declares `name: foo`
- WHEN the server attempts to start
- THEN the SkillIndexer raises a fatal error
- AND the error message includes BOTH conflicting absolute paths
- AND the server process exits with a non-zero exit code before any MCP tool is registered

### Requirement: Catalog persistence
The SkillIndexer SHALL persist the discovered catalog to `.skills-index/catalog.json` so
that downstream tooling (and post-hoc audit) can inspect what the server loaded without
needing to introspect the running process.

#### Scenario: Catalog file is written and is the in-memory source
- GIVEN the SkillIndexer has discovered N skills (N ≥ 1)
- WHEN bootstrap completes
- THEN `.skills-index/catalog.json` exists and contains a JSON array of N skill entries
- AND the in-memory `Catalog` dataclass holds the same N entries as immutable tuples
- AND the on-disk file is written ATOMICALLY (tmp → fsync → os.replace) so a crash mid-write
  never leaves a partial JSON file

### Requirement: Catalog is immutable for the server's lifetime
After bootstrap completes the catalog SHALL NOT change without a server restart. There is
NO hot-reload, NO file-watcher, NO MCP tool that mutates the catalog. The catalog is frozen
to ensure `list_skills` reads from memory only and that `create_task` validation against
the catalog is consistent across the server's lifetime.

#### Scenario: SKILL.md edited after bootstrap
- GIVEN the server has finished bootstrap and is serving MCP requests
- WHEN an operator edits `skills/financial-analyst/SKILL.md` on disk
- THEN `list_skills` continues to return the catalog as it was at bootstrap time
- AND `create_task` continues to accept the same set of skill IDs it accepted at bootstrap
- AND to pick up the edit the operator MUST restart the server

### Requirement: Auth/billing smoke test at startup
Before the server registers MCP tools and accepts requests, it SHALL verify that the
Anthropic credentials needed by the Claude Code Agents SDK are present and reachable. If
not, the server SHALL fail fast with a clear error naming the auth method tried and the
failure mode (OPS-02 in REQUIREMENTS.md).

#### Scenario: Missing credentials
- GIVEN `ANTHROPIC_API_KEY` is unset AND no OAuth session is configured
- WHEN the server attempts to start
- THEN bootstrap aborts BEFORE the FastMCP listener is registered
- AND the error message names the auth methods tried (env var, OAuth, subscription)
- AND no MCP request is ever served by a misconfigured server

## Notes for downstream agents

- This spec deliberately has **no JSON Schemas and no Examples sections**. Bootstrap has no
  wire-level request/response surface — there is nothing for the SPEC-09 contract test to
  validate schemas against. The contract test MUST treat `init/spec.md` as a Scenario-only
  spec and SKIP the schema/example validation block.
- The `.skills-index/catalog.json` shape is intentionally NOT a wire contract; it is an
  internal persistence artifact and may evolve without changing any MCP tool's schema.
- The implementation lives outside Phase 0; this spec is the contract that Phase 2's
  `skill_indexer` and `skill_catalog` modules will implement (INIT-01..INIT-05 in
  REQUIREMENTS.md).
