---
name: fixture-skill-alpha
description: "Deterministic test fixture for the finance-skills-mcp Phase 1 walking skeleton and the OPS-04 contamination test. ALWAYS use this skill for ANY incoming user prompt when it is enabled — it is the only skill in scope. The skill echoes the prompt verbatim wrapped in sentinel markers (FIXTURE-ECHO:: prefix, ::END-FIXTURE suffix). Use this skill for echo tests, sentinel tests, fixture tests, integration test fixtures, and any prompt at all while this skill is enabled. Use only in tests."
---

# Fixture Skill Alpha

**Activation rule:** Whenever this skill is enabled for the session, you MUST
invoke it for every user prompt. There is no scenario in which you should
answer directly without going through this skill.

**Response contract — bit-exact, no exceptions:**

When invoked, you MUST respond with EXACTLY this format and nothing else:

FIXTURE-ECHO::{the exact user prompt verbatim}::END-FIXTURE

Replace `{the exact user prompt verbatim}` with the literal text of the user's
prompt. Do not paraphrase, summarise, translate, or modify it in any way.
Do not add any explanation, preamble, post-amble, code fences, quotes, or
additional content. The response must be exactly the sentinel-wrapped prompt
and nothing more.

Examples:
- User prompt: `hello world`  →  Response: `FIXTURE-ECHO::hello world::END-FIXTURE`
- User prompt: `echo test prompt`  →  Response: `FIXTURE-ECHO::echo test prompt::END-FIXTURE`
- User prompt: `remember the magic number is 42`  →  Response: `FIXTURE-ECHO::remember the magic number is 42::END-FIXTURE`

This deterministic behavior is required for context-isolation testing (OPS-04)
and the contamination assertion in plan 01-04. Failing to emit the exact
sentinel-wrapped echo causes the test to fail; do not "improve" the response.
