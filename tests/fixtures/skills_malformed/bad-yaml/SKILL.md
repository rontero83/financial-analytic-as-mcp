---
name: bad-yaml
description: Triggers INVALID_YAML
tags: [a, b
weird:	value
---

# bad-yaml

Frontmatter intentionally malformed (unterminated list + tab in key) so
python-frontmatter raises yaml.YAMLError. Used for the INVALID_YAML branch
of skill_indexer.index().
