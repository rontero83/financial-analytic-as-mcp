---
name: Invalid_Name_With_UPPERCASE
description: Fixture for the INVALID_NAME branch — name violates D-29 regex.
---

# invalid-name-UPPER

Frontmatter `name:` contains uppercase letters AND underscores, both
forbidden by the D-29 regex `^[a-z0-9][a-z0-9-]*[a-z0-9]$`. Used for the
INVALID_NAME branch of skill_indexer.index().
