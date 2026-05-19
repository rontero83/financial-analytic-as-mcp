"""Phase 2 integration acceptance — ROADMAP SC1..SC5.

Covers UNIV-01, UNIV-02, INIT-03, INIT-04, INIT-05.

Each test stages skill roots under ``tmp_path`` and points
``FSMC_SKILL_ROOTS`` at them (D-35) — the production ``skills/`` tree is
never mutated. Tests that drive ``app_lifespan`` to a fatal exit go
through ``async with app_lifespan(mcp) as ctx:`` + ``pytest.raises(SystemExit)``;
all other tests use ``async with Client(mcp) as client:`` against the
in-memory FastMCP transport with ``MockAgentRunner`` substituting for the
Claude Agent SDK (zero API spend, < 5 s total runtime).

The SC → test mapping is:

  SC1 → test_sc1_drop_new_skill_appears_in_list_and_invocable
  SC2 → test_sc2_remove_skill_disappears_on_restart
  SC3 → test_sc3_duplicate_name_across_roots_blocks_startup_with_both_paths_in_stderr
  SC4 (positive) → test_sc4_malformed_skipped_others_indexed
  SC4 (inverse)  → test_sc4_inverse_all_malformed_blocks_startup
  SC5 → test_sc5_catalog_persisted_and_no_runtime_scan
         + test_sc5_catalog_is_frozen_after_lifespan_entry (immutability assertion)

The autouse ``_snapshot_repo_skills_index`` fixture (conftest.py) handles M-4 from
02-PLAN-CHECK — the lifespan writes ``.skills-index/`` into the real repo root,
which the fixture snapshots and restores around every test.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import shutil
from pathlib import Path

import anyio
import pytest
from fastmcp import Client

from finance_skills_mcp import agent_runner as _agent_runner_module
from finance_skills_mcp.server import app_lifespan, mcp
from finance_skills_mcp.skill_index_store import INDEX_DIR_NAME
from tests._fixtures.mock_agent_runner import MockAgentRunner
from tests.integration_in_memory._indexing_helpers import (
    prime_auth,
    set_fsmc_skill_roots,
    write_malformed_skill,
    write_skill,
)
from tests.integration_live._client_helpers import extract_data, is_error


pytestmark = [pytest.mark.in_memory, pytest.mark.anyio]


# D-03 task_id format: YYYYMMDDTHHMMSS-<8 hex>
TASK_ID_RE_PATTERN = r"^[0-9]{8}T[0-9]{6}-[0-9a-f]{8}$"

# Repo root is computed the same way the production lifespan computes it
# (Path(server.__file__).resolve().parents[2]). The lifespan persists
# .skills-index/ under this path; tests assert against the same location.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_INDEX_DIR = _REPO_ROOT / INDEX_DIR_NAME


# ---------------------------------------------------------------------------
# SC1 — UNIV-01 acceptance: drop a skill, restart, list_skills + create_task work
# ---------------------------------------------------------------------------


async def test_sc1_drop_new_skill_appears_in_list_and_invocable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """SC1 / UNIV-01: a server started with FSMC_SKILL_ROOTS pointing at a
    tmp_path root containing ``aaaa-test-skill-zzzz`` exposes that skill in
    list_skills AND create_task succeeds with it (UNIV-01 acceptance via D-35
    fixture-style override; no production src/ files modified by this test —
    verified implicitly via the test asserting purely against the wire shape).
    """
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "aaaa-test-skill-zzzz")
    # Second skill so the catalog has at least 2 entries — proves the indexer
    # isn't producing a single-entry artefact.
    write_skill(skills_root, "fixture-skill-alpha")

    prime_auth(monkeypatch)
    set_fsmc_skill_roots(monkeypatch, skills_root)

    canned = "ok"
    mock_runner = MockAgentRunner(canned_output=canned)
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    async with Client(mcp) as client:
        ls = await client.call_tool("list_skills", {})
        assert not is_error(ls)
        ls_data = extract_data(ls)
        skill_ids = {s["id"] for s in ls_data["skills"]}
        assert "aaaa-test-skill-zzzz" in skill_ids, (
            f"aaaa-test-skill-zzzz missing from catalog: {skill_ids}"
        )

        ct = await client.call_tool(
            "create_task",
            {"prompt": "hello", "skills": ["aaaa-test-skill-zzzz"]},
        )
        assert not is_error(ct)
        ct_data = extract_data(ct)
        task_id = ct_data.get("task_id")
        assert task_id is not None and re.match(TASK_ID_RE_PATTERN, task_id), (
            f"unexpected task_id shape: {task_id!r}"
        )

        # Poll until terminal — MockAgentRunner returns synchronously so
        # the working -> completed transition is near-instant.
        final_status = None
        for _ in range(40):
            st = await client.call_tool("get_task_status", {"task_id": task_id})
            st_data = extract_data(st)
            if st_data.get("status") in {"completed", "failed"}:
                final_status = st_data["status"]
                break
            await asyncio.sleep(0.05)
        assert final_status == "completed", (
            f"expected completed; got {final_status!r}"
        )

        # L-1 (from 02-PLAN-CHECK): pin the canned output through get_task_result.
        gr = await client.call_tool("get_task_result", {"task_id": task_id})
        assert not is_error(gr)
        gr_data = extract_data(gr)
        assert canned in gr_data.get("output_markdown", ""), (
            f"canned output {canned!r} missing from "
            f"output_markdown={gr_data.get('output_markdown')!r}"
        )

        # MockAgentRunner was actually invoked — proves the DI wiring is live.
        assert len(mock_runner.calls) == 1


# ---------------------------------------------------------------------------
# SC2 — UNIV-02: remove a skill, restart, it's gone
# ---------------------------------------------------------------------------


async def test_sc2_remove_skill_disappears_on_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """SC2 / UNIV-02: a server started against a tmp_path root with skills
    {alpha, beta} exposes both; after the skill ``beta`` dir is removed,
    a fresh ``async with Client(mcp)`` (which re-enters app_lifespan)
    exposes only {alpha}. Proves the catalog is rebuilt on each lifespan
    entry — INIT-04 "immutable for lifetime" + UNIV-02 "removal works".
    """
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "alpha")
    write_skill(skills_root, "beta")

    prime_auth(monkeypatch)
    set_fsmc_skill_roots(monkeypatch, skills_root)
    mock_runner = MockAgentRunner(canned_output="ok")
    monkeypatch.setattr(_agent_runner_module, "run", mock_runner.run)

    # First lifespan cycle: both skills present.
    async with Client(mcp) as client:
        ls = await client.call_tool("list_skills", {})
        names_before = {s["name"] for s in extract_data(ls)["skills"]}
    assert names_before == {"alpha", "beta"}, (
        f"first-cycle names: {names_before}"
    )

    # "Restart" — delete beta and re-enter the in-memory client (lifespan re-runs).
    shutil.rmtree(skills_root / "beta")
    assert not (skills_root / "beta").exists()

    async with Client(mcp) as client:
        ls = await client.call_tool("list_skills", {})
        names_after = {s["name"] for s in extract_data(ls)["skills"]}
    assert names_after == {"alpha"}, (
        f"second-cycle names: {names_after} (beta should be gone)"
    )


# ---------------------------------------------------------------------------
# SC3 — INIT-03: duplicate name across roots blocks startup with both paths in stderr
# ---------------------------------------------------------------------------


async def test_sc3_duplicate_name_across_roots_blocks_startup_with_both_paths_in_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
):
    """SC3 / INIT-03 / D-32: two roots with the same skill ``name`` cause
    ``app_lifespan`` to ``sys.exit(3)`` BEFORE any MCP tool is reachable.
    Stderr contains BOTH absolute SKILL.md paths. ``.skills-index/errors.json``
    captures both DUPLICATE_NAME entries (the persist step runs before the
    fatal exit per plan 02-03's lifespan ordering).
    """
    root_a = tmp_path / "roota"
    root_b = tmp_path / "rootb"
    path_a = write_skill(root_a, "dup", description="Duplicate fixture (a)")
    path_b = write_skill(root_b, "dup", description="Duplicate fixture (b)")
    abs_path_a = str(path_a.resolve())
    abs_path_b = str(path_b.resolve())

    prime_auth(monkeypatch)
    set_fsmc_skill_roots(monkeypatch, root_a, root_b)

    with pytest.raises(SystemExit) as exc_info:
        async with app_lifespan(mcp) as _ctx:
            pass  # pragma: no cover — lifespan must exit before yielding

    assert exc_info.value.code == 3, (
        f"expected SystemExit(3) for DUPLICATE_NAME; got code={exc_info.value.code!r}"
    )

    captured = capsys.readouterr()
    assert abs_path_a in captured.err, (
        f"stderr missing absolute path A ({abs_path_a}); err={captured.err!r}"
    )
    assert abs_path_b in captured.err, (
        f"stderr missing absolute path B ({abs_path_b}); err={captured.err!r}"
    )

    # .skills-index/errors.json must have both DUPLICATE_NAME entries.
    errors_payload = json.loads((_INDEX_DIR / "errors.json").read_text(encoding="utf-8"))
    dup_paths = {
        entry["path"]
        for entry in errors_payload
        if entry.get("error_code") == "DUPLICATE_NAME"
    }
    assert abs_path_a in dup_paths and abs_path_b in dup_paths, (
        f"errors.json missing DUPLICATE_NAME paths; got {dup_paths}"
    )


# ---------------------------------------------------------------------------
# SC4 (positive) — INIT-05: malformed skipped, others indexed, errors.json populated
# ---------------------------------------------------------------------------


async def test_sc4_malformed_skipped_others_indexed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """SC4 (positive) / INIT-05: a root containing one valid SKILL.md and
    one malformed SKILL.md STARTS successfully; list_skills returns only
    the valid skill; ``.skills-index/errors.json`` contains the malformed
    entry classified as INVALID_YAML.
    """
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "good")
    bad_path = write_malformed_skill(skills_root, "bad", kind="bad_yaml")
    abs_bad = str(bad_path.resolve())

    prime_auth(monkeypatch)
    set_fsmc_skill_roots(monkeypatch, skills_root)
    monkeypatch.setattr(
        _agent_runner_module, "run", MockAgentRunner(canned_output="ok").run
    )

    async with Client(mcp) as client:
        ls = await client.call_tool("list_skills", {})
        names = {s["name"] for s in extract_data(ls)["skills"]}
    assert names == {"good"}, (
        f"expected only 'good' after malformed skip; got {names}"
    )

    errors_payload = json.loads((_INDEX_DIR / "errors.json").read_text(encoding="utf-8"))
    matching = [
        e
        for e in errors_payload
        if e.get("error_code") == "INVALID_YAML" and e.get("path") == abs_bad
    ]
    assert len(matching) == 1, (
        f"expected exactly one INVALID_YAML entry for {abs_bad}; got {errors_payload}"
    )


# ---------------------------------------------------------------------------
# SC4 (inverse) — D-33: all-malformed roots block startup with summary on stderr
# ---------------------------------------------------------------------------


async def test_sc4_inverse_all_malformed_blocks_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
):
    """SC4 (inverse) / D-33: a root containing only malformed SKILL.md files
    causes ``app_lifespan`` to ``sys.exit(4)``; stderr contains 'NO VALID SKILLS'
    and the per-code Counter mentions both error codes;
    ``.skills-index/errors.json`` has two entries.
    """
    skills_root = tmp_path / "skills"
    write_malformed_skill(skills_root, "bad1", kind="missing_name")
    write_malformed_skill(skills_root, "bad2", kind="missing_description")

    prime_auth(monkeypatch)
    set_fsmc_skill_roots(monkeypatch, skills_root)

    with pytest.raises(SystemExit) as exc_info:
        async with app_lifespan(mcp) as _ctx:
            pass  # pragma: no cover — lifespan must exit before yielding

    assert exc_info.value.code == 4, (
        f"expected SystemExit(4); got code={exc_info.value.code!r}"
    )

    captured = capsys.readouterr()
    assert "NO VALID SKILLS" in captured.err, (
        f"stderr missing 'NO VALID SKILLS' summary; err={captured.err!r}"
    )
    assert "MISSING_NAME" in captured.err, (
        f"stderr Counter missing MISSING_NAME; err={captured.err!r}"
    )
    assert "MISSING_DESCRIPTION" in captured.err, (
        f"stderr Counter missing MISSING_DESCRIPTION; err={captured.err!r}"
    )

    errors_payload = json.loads((_INDEX_DIR / "errors.json").read_text(encoding="utf-8"))
    codes = [e.get("error_code") for e in errors_payload]
    assert codes.count("MISSING_NAME") == 1, (
        f"expected one MISSING_NAME entry; got {codes}"
    )
    assert codes.count("MISSING_DESCRIPTION") == 1, (
        f"expected one MISSING_DESCRIPTION entry; got {codes}"
    )


# ---------------------------------------------------------------------------
# SC5 — INIT-04: catalog persisted to disk, no runtime re-scan
# ---------------------------------------------------------------------------


async def test_sc5_catalog_persisted_and_no_runtime_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """SC5 / INIT-04: (a) ``.skills-index/catalog.json`` on disk matches the
    ``list_skills`` wire payload; (b) editing a SKILL.md after the lifespan
    has yielded does NOT change subsequent ``list_skills`` results — proves
    no runtime disk re-scan; the in-memory frozen Catalog is the lifetime
    source of truth.
    """
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "alpha", description="alpha original desc")
    write_skill(skills_root, "beta", description="beta original desc")

    prime_auth(monkeypatch)
    set_fsmc_skill_roots(monkeypatch, skills_root)
    monkeypatch.setattr(
        _agent_runner_module, "run", MockAgentRunner(canned_output="ok").run
    )

    async with Client(mcp) as client:
        ls = await client.call_tool("list_skills", {})
        wire_first = extract_data(ls)
        assert {s["name"] for s in wire_first["skills"]} == {"alpha", "beta"}

        # Disk catalog.json must mirror the wire payload (SC5 inspectability).
        on_disk = json.loads((_INDEX_DIR / "catalog.json").read_text(encoding="utf-8"))
        assert on_disk == wire_first, (
            "on-disk catalog.json must equal the list_skills wire payload"
        )

        # Mutate the source SKILL.md mid-lifespan — must NOT affect the
        # in-memory catalog (INIT-04 immutability + no runtime re-scan).
        write_skill(skills_root, "alpha", description="alpha MUTATED desc")
        ls_again = extract_data(await client.call_tool("list_skills", {}))
        alpha_entry = next(
            s for s in ls_again["skills"] if s["name"] == "alpha"
        )
        assert alpha_entry["description"] == "alpha original desc", (
            f"alpha desc unexpectedly refreshed mid-lifespan: "
            f"{alpha_entry['description']!r}"
        )


async def test_sc5_catalog_is_frozen_after_lifespan_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """SC5 (immutability arm): the Catalog yielded by ``app_lifespan`` is a
    frozen dataclass — mutating its ``skills`` tuple raises
    ``dataclasses.FrozenInstanceError`` AND the tuple itself rejects item
    assignment.
    """
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "alpha")
    write_skill(skills_root, "beta")

    prime_auth(monkeypatch)
    set_fsmc_skill_roots(monkeypatch, skills_root)

    async with app_lifespan(mcp) as ctx:
        catalog = ctx["catalog"]
        assert isinstance(catalog.skills, tuple), (
            f"Catalog.skills should be a tuple; got {type(catalog.skills)}"
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            catalog.skills = ()  # type: ignore[misc]
        with pytest.raises(TypeError):
            catalog.skills[0] = catalog.skills[0]  # type: ignore[index]
