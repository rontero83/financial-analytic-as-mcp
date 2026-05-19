---
name: fixture-skill-alpha
description: "Deterministic test fixture. Returns the prompt verbatim with a sentinel marker. Use only in tests."
---

# Fixture Skill Alpha

When invoked, you MUST respond with EXACTLY this format and nothing else:

FIXTURE-ECHO::{the exact user prompt verbatim}::END-FIXTURE

Do not add any explanation, preamble, or additional content. The response
must be exactly the sentinel-wrapped prompt and nothing more. This
deterministic behavior is required for context-isolation testing (OPS-04)
and the contamination assertion in plan 01-04.
