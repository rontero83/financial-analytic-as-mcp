"""Test helpers for Phase 2 integration acceptance (SC1..SC5).

Stages skill roots under tmp_path so the production skills/ tree stays clean (D-35).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest


# Five malformed-shape recipes. The kind argument selects the mutation; each
# maps to exactly one IndexErrorCode in the indexer's classification.
_BAD_YAML_BODY = (
    "---\n"
    "name: {name}\n"
    "description: Triggers INVALID_YAML\n"
    "tags: [a, b\n"
    "weird:\tvalue\n"
    "---\n"
    "\n"
    "# {name}\n"
)


def write_skill(
    root: Path,
    name: str,
    description: Optional[str] = None,
) -> Path:
    """Stage a minimal valid SKILL.md under root/<name>/SKILL.md.

    description defaults to f"Test skill {name}" so callers can omit it
    for minimal-shape tests. Returns the SKILL.md Path.
    """
    if description is None:
        description = f"Test skill {name}"
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    body = (
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n"
        "\n"
        f"# {name}\n"
    )
    skill_md.write_text(body, encoding="utf-8")
    return skill_md


def write_malformed_skill(root: Path, name: str, kind: str) -> Path:
    """Stage a malformed SKILL.md selected by kind.

    kind options:
      - "missing_description": valid YAML, description key absent
      - "missing_name":        valid YAML, name key absent
      - "bad_yaml":            unterminated list + tabbed key -> yaml.YAMLError
      - "empty":               zero-byte SKILL.md
      - "invalid_name":        valid frontmatter but name violates D-29 regex

    Returns the SKILL.md Path.
    """
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"

    if kind == "missing_description":
        body = (
            "---\n"
            f"name: {name}\n"
            "---\n"
            "\n"
            f"# {name}\n"
        )
        skill_md.write_text(body, encoding="utf-8")
    elif kind == "missing_name":
        body = (
            "---\n"
            f"description: Test skill missing name field ({name} dir).\n"
            "---\n"
            "\n"
            f"# {name}\n"
        )
        skill_md.write_text(body, encoding="utf-8")
    elif kind == "bad_yaml":
        # Mirrors the canonical bad-yaml fixture pattern from plan 02-01:
        # unterminated list + tab-after-colon → yaml.YAMLError.
        skill_md.write_text(_BAD_YAML_BODY.format(name=name), encoding="utf-8")
    elif kind == "empty":
        skill_md.write_text("", encoding="utf-8")
    elif kind == "invalid_name":
        # D-29 violation: uppercase + underscores forbidden.
        body = (
            "---\n"
            'name: Invalid_UPPER_Name\n'
            f"description: Test skill in dir {name} with invalid frontmatter name.\n"
            "---\n"
            "\n"
            "# Invalid name fixture\n"
        )
        skill_md.write_text(body, encoding="utf-8")
    else:  # pragma: no cover — defensive
        raise ValueError(f"unknown malformed kind: {kind!r}")

    return skill_md


def set_fsmc_skill_roots(monkeypatch: pytest.MonkeyPatch, *roots: Path) -> str:
    """Monkeypatch os.environ['FSMC_SKILL_ROOTS'] to the colon-joined abs paths.

    Returns the joined string for assertion convenience.
    """
    joined = ":".join(str(Path(r).resolve()) for r in roots)
    monkeypatch.setenv("FSMC_SKILL_ROOTS", joined)
    return joined


def prime_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set ANTHROPIC_API_KEY to a Phase-2 sentinel so the D-12 auth gate passes."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock-in-memory-phase-2-key")
