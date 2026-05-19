# skills_malformed/ — fixture → IndexErrorCode mapping

Phase 2 (plan 02-01 Task 2). Each subdirectory contains exactly one
`SKILL.md` that triggers exactly one `IndexErrorCode` when fed to
`skill_indexer.index()`. The unit suite in `tests/unit/test_skill_indexer.py`
parametrizes over this mapping; do NOT add a second mutation per fixture
without also adding a new subdirectory.

| Subdirectory               | Target IndexErrorCode | Mutation                                                                                  |
|----------------------------|-----------------------|-------------------------------------------------------------------------------------------|
| `bad-yaml/`                | `INVALID_YAML`        | Unterminated list + tabbed key in YAML frontmatter → `yaml.YAMLError`                     |
| `missing-description/`     | `MISSING_DESCRIPTION` | Valid YAML, `description` key absent                                                      |
| `missing-name/`            | `MISSING_NAME`        | Valid YAML, `name` key absent                                                             |
| `invalid-name-UPPER/`      | `INVALID_NAME`        | `name: Invalid_Name_With_UPPERCASE` violates D-29 regex                                   |
| `empty-file/`              | `EMPTY_FILE`          | Zero-byte SKILL.md                                                                        |
| `unknown-fields/`          | `UNKNOWN_FIELD`       | Valid skill PLUS extra key `weird_extra: 42` not in D-28 whitelist (skill IS still indexed) |

Codes NOT covered here (deliberately):

- `ENCODING_ERROR` — exercised in the unit suite via `tmp_path` writing
  latin-1 bytes; needs no on-disk fixture.
- `DUPLICATE_NAME` — cross-root concern; exercised in the unit suite via
  `tmp_path` creating two roots with the same `name:`. Plan 02-03's
  integration test covers the server-fatal handling.
- `INVALID_PATH` — symlink-escape concern; exercised in the unit suite via
  `tmp_path` creating an escaping symlink (POSIX-only, see D-09).

The D-28 whitelist of recognized optional frontmatter keys is:
`version`, `tags`, `scripts`, `references`. Any other key emits one
`UNKNOWN_FIELD` warning per offending key — but the skill is STILL
indexed (warning, not error severity).
