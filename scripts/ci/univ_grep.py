#!/usr/bin/env python3
"""UNIV-03 / D-18 — forbid real skill names as literals in ``src/``.

Why: Phase 1 implements a *universal* MCP server — code in ``src/`` MUST NOT
branch on (or otherwise reference) the string identity of any real skill.
Every real skill is opaque to the server: it is just an entry in the
catalog. Phase 2's dynamic indexer will produce the same shape; the
server-side code MUST keep treating skill names as opaque opaque labels.

How this guard works:
1. Walk ``skills/*/SKILL.md`` and parse the YAML frontmatter via
   ``python-frontmatter``. Extract the ``name`` field from each.
2. For each derived name, search every ``*.py`` file under ``src/`` for a
   string-literal occurrence of that name (we use Python's ``ast`` module,
   not raw grep, so we only match real ``str`` literals — not substrings
   inside docstrings, comments, or identifiers like ``finance-skills-mcp``
   that happen to contain a skill name as a hyphenated substring).
3. If ANY match, print offenders and exit 1. Otherwise exit 0.

Why AST and not ``grep -Frnw``: the package itself is named
``finance-skills-mcp``, which contains ``finance-skills`` as a hyphen-bounded
substring. GNU ``grep -w`` treats ``-`` as a word boundary, so a naive grep
would yield false positives on every module that imports from
``finance_skills_mcp`` (under-score variant — no false positive there) or
on the literal package label ``"finance-skills-mcp"`` in
``server.py`` (false positive). AST-based literal extraction matches
EXACTLY the strings the developer wrote, so ``"finance-skills-mcp"`` is
one string literal — it never matches the ``finance-skills`` skill name
unless the literal is exactly ``finance-skills``.

Exclusions:
- The fixture skill ``fixture-skill-alpha`` lives under
  ``tests/fixtures/skills/``, not ``skills/`` — by construction it is NOT
  on the UNIV-03 deny-list (Phase 1 catalog seed references it; that's
  fine. RESEARCH.md §Pitfall 11 explicitly notes this is OK because
  ``skills/*`` is the prod catalog source for Phase 2+.)

Exit code: 0 if clean, 1 if any literal match is found.

Reference: 01-04-PLAN.md Task 3 (universality grep); 01-CONTEXT.md D-18;
01-RESEARCH.md §Universality CI Grep Job; 01-01-SUMMARY.md §A6 (verified
``python-frontmatter`` API).
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# python-frontmatter is a dev dep (pyproject.toml [dependency-groups].dev);
# this script runs under `uv run`, which makes dev deps importable.
import frontmatter


def discover_skill_names(skills_root: Path) -> list[str]:
    """Walk ``skills_root``/*/SKILL.md, return list of frontmatter ``name`` values."""
    names: list[str] = []
    for skill_md in sorted(skills_root.glob("*/SKILL.md")):
        # Defensive: never accept anything under tests/fixtures/ even if it
        # somehow ended up under the discovery root.
        if "tests/fixtures" in str(skill_md):
            continue
        try:
            post = frontmatter.load(str(skill_md))
        except Exception as exc:  # noqa: BLE001 — surface as CI error
            print(
                f"::error file={skill_md}::Failed to parse SKILL.md frontmatter: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            continue
        name = post.metadata.get("name")
        if not name:
            print(
                f"::warning file={skill_md}::SKILL.md missing 'name' frontmatter "
                f"field; skipping UNIV-03 check for this file",
                file=sys.stderr,
            )
            continue
        names.append(str(name).strip())
    return names


def find_literal_in_file(path: Path, needles: set[str]) -> list[tuple[int, int, str]]:
    """Return list of (line, col, matched_name) for every str literal in ``path``
    whose value matches any element of ``needles`` exactly."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        print(
            f"::error file={path},line={exc.lineno or 0}::"
            f"SyntaxError parsing {path}: {exc.msg}",
            file=sys.stderr,
        )
        return []
    hits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        # ast.Constant with a str value covers every string literal in
        # modern Python (3.8+). We do NOT descend into f-strings'
        # FormattedValue parts — those are expressions, not literal
        # references to a skill name.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in needles:
                hits.append((node.lineno, node.col_offset, node.value))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid real skill names as literals in src/ (UNIV-03)."
    )
    parser.add_argument(
        "--skills-root",
        default="skills",
        help="Directory containing <name>/SKILL.md per skill (default: skills).",
    )
    parser.add_argument(
        "--src-root",
        default="src",
        help="Directory to scan for literal matches (default: src).",
    )
    args = parser.parse_args(argv)

    skills_root = Path(args.skills_root)
    src_root = Path(args.src_root)
    if not skills_root.is_dir():
        print(f"::error::skills-root {skills_root} is not a directory", file=sys.stderr)
        return 1
    if not src_root.is_dir():
        print(f"::error::src-root {src_root} is not a directory", file=sys.stderr)
        return 1

    names = discover_skill_names(skills_root)
    if not names:
        print(
            f"::error::No skill names extracted from {skills_root}/*/SKILL.md — "
            f"UNIV-03 guard cannot run.",
            file=sys.stderr,
        )
        return 1
    print(f"UNIV-03: derived {len(names)} skill name(s) from {skills_root}/: {names}")

    needles = set(names)
    all_hits: list[tuple[Path, int, int, str]] = []
    for py_file in sorted(src_root.rglob("*.py")):
        for line, col, name in find_literal_in_file(py_file, needles):
            all_hits.append((py_file, line, col, name))

    if not all_hits:
        print(f"OK: no real skill name literals found in {src_root}/")
        return 0

    for path, line, col, name in all_hits:
        print(
            f"::error file={path},line={line},col={col}::"
            f"UNIV-03 violation in {path}:{line}:{col}: string literal "
            f"{name!r} matches real skill name (skills/{name}/SKILL.md). "
            f"Code in {src_root}/ MUST treat skill names as opaque labels — "
            f"do not branch on or hardcode them.",
            file=sys.stderr,
        )
    print(
        f"FAIL: {len(all_hits)} real skill-name literal(s) found in {src_root}/",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
