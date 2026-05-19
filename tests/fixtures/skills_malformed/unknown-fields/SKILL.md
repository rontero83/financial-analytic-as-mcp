---
name: unknown-fields
description: Fixture for the UNKNOWN_FIELD warning — valid skill plus extra key.
weird_extra: 42
---

# unknown-fields

Frontmatter is valid (both required keys present and well-formed) but
carries an extra `weird_extra` key not in the D-28 whitelist
{`version`, `tags`, `scripts`, `references`}. The indexer emits a
UNKNOWN_FIELD warning AND still indexes the skill.
