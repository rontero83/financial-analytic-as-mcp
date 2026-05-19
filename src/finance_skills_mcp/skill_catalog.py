"""Frozen in-memory skill catalog dataclasses (D-13, D-14, D-17).

Phase 2 / D-34 retired the Phase-1 hardcoded seed function that lived in this
module — the catalog is now produced exclusively by
:func:`finance_skills_mcp.skill_indexer.index`, which walks every root in
``FSMC_SKILL_ROOTS`` (default ``skills/``) and validates each ``SKILL.md``
frontmatter block. Persistence of the resulting catalog to
``.skills-index/catalog.json`` is owned by
:func:`finance_skills_mcp.skill_index_store.persist_index`. Nothing in this
module touches disk, the environment, or the FastMCP lifespan; it just owns
the ``Skill`` and ``Catalog`` wire shapes.

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
