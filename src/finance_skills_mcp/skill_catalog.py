"""Frozen in-memory skill catalog + Phase 1 seed (D-13, D-14, D-17).

Phase 1 wires the catalog by hand from ``_seed_catalog()`` — exactly ONE
fixture entry (``fixture-skill-alpha``). Phase 2 swaps the seed for the
dynamic disk-walking indexer; the ``Catalog`` and ``Skill`` shapes stay
unchanged.

The ``Skill.path`` field is a **relative string label** per RESEARCH.md
Pitfall 9 — clients pick skills by ``name``, not by ``path``; the wire
schema does not require ``path`` to resolve to a real directory in
production deployments.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Skill:
    """One entry in the in-memory catalog.

    Wire-equivalent shape per ``specs/list-skills/spec.md``::

        {"id": str, "name": str, "description": str, "path": str}
    """

    id: str
    name: str
    description: str
    path: str  # relative string label; not enforced to exist on disk

    def to_wire_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "path": self.path,
        }


@dataclass(frozen=True)
class Catalog:
    """Immutable catalog. Frozen for the server's lifetime (init/spec.md)."""

    skills: tuple[Skill, ...]


def _seed_catalog() -> Catalog:
    """D-14/D-17: Phase 1 hardcodes one fixture skill entry.

    Phase 2 replaces this with ``skill_indexer.index()`` (disk walk over
    ``skills/*/SKILL.md``).
    """
    return Catalog(
        skills=(
            Skill(
                id="fixture-skill-alpha",
                name="fixture-skill-alpha",
                description=(
                    "Deterministic test fixture. Returns the prompt verbatim "
                    "with a sentinel marker. Use only in tests."
                ),
                path="tests/fixtures/skills/fixture-skill-alpha",
            ),
        )
    )


# Public alias — the canonical name used by server.py lifespan.
seed_catalog = _seed_catalog
