"""Unit tests for server.app_lifespan + _parse_skill_roots_env (Phase 2; D-23, D-32, D-33, INIT-01, INIT-03, INIT-04).

Covers:
- _parse_skill_roots_env across unset / empty / single / multi / whitespace
  / absolute-segment cases (D-23 / D-24).
- The lifespan happy path — the yielded ``catalog`` is the exact frozen
  ``Catalog`` instance the indexer produced (INIT-04 / SC5 — no disk
  re-scan; identity check, not equality).
- The duplicate-name fatal — ``IndexErrorCode.DUPLICATE_NAME`` triggers
  ``SystemExit(3)`` AND both conflicting absolute paths land on stderr
  AND ``persist_index`` ran BEFORE the exit (D-32 / INIT-03 / SC3).
- The empty-catalog fatal — empty ``Catalog`` triggers ``SystemExit(4)``
  AND stderr carries the per-code Counter summary AND ``persist_index``
  ran BEFORE the exit (D-33 / SC4-inverse).
- The auth gate (D-12 / OPS-02) still fires FIRST — ``index_skills`` is
  never reached when auth is absent.
- The yielded ``Catalog`` is frozen (Skill/Catalog still frozen after the
  wiring change; mutation raises ``FrozenInstanceError``).

These tests do NOT drive the full FastMCP Client — they exercise
``app_lifespan(mcp)`` directly via its async-context-manager surface.
The full end-to-end SC1..SC5 acceptance lives in plan 02-04's
``tests/integration_in_memory/test_universal_indexing.py``.
"""
from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Any

import pytest

from finance_skills_mcp.errors import IndexErrorCode
from finance_skills_mcp.server import _parse_skill_roots_env, app_lifespan, mcp
from finance_skills_mcp.skill_catalog import Catalog, Skill
from finance_skills_mcp.skill_indexer import IndexError as IxErr, IndexResult


pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(name: str) -> Skill:
    """Build a Skill with deterministic fields for catalog construction."""
    return Skill(
        id=name,
        name=name,
        description=f"d-{name}",
        path=f"tests/fixtures/skills/{name}",
    )


def _make_dup_error(name: str, path: str) -> IxErr:
    """Build a DUPLICATE_NAME IndexError for the fatal-path tests."""
    return IxErr(
        path=path,
        error_code=IndexErrorCode.DUPLICATE_NAME,
        message=f"duplicate skill name {name!r}",
    )


def _make_recording_persist(call_log: list[dict[str, Any]]):
    """Return a fake ``persist_index`` that records its calls into call_log.

    The recorded entry carries a monotonic timestamp so the "persist ran
    BEFORE exit" assertion can order it against the SystemExit timestamp.
    """

    def _fake_persist(result: IndexResult, index_dir: Path) -> None:
        call_log.append(
            {
                "ts": time.monotonic(),
                "result_skill_count": len(result.catalog.skills),
                "result_error_count": len(result.errors),
                "index_dir": str(index_dir),
            }
        )

    return _fake_persist


def _prime_auth(monkeypatch) -> None:
    """Satisfy the D-12 / OPS-02 auth smoke test so lifespan can proceed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unit-test-sentinel-key")


def _patch_indexer(monkeypatch, result: IndexResult, calls: list[tuple] | None = None):
    """Replace finance_skills_mcp.server.index_skills with a fake.

    The fake captures (roots,) tuples into ``calls`` if supplied — used by
    the auth-precedence test to assert the indexer never fired.
    """

    def _fake_index(roots):
        if calls is not None:
            calls.append((roots,))
        return result

    monkeypatch.setattr("finance_skills_mcp.server.index_skills", _fake_index)


def _patch_persist(monkeypatch, call_log: list[dict[str, Any]]):
    monkeypatch.setattr(
        "finance_skills_mcp.server.persist_index",
        _make_recording_persist(call_log),
    )


# ---------------------------------------------------------------------------
# _parse_skill_roots_env — D-23 / D-24
# ---------------------------------------------------------------------------


def test_parse_skill_roots_env_default_when_unset(tmp_path: Path) -> None:
    """Env without FSMC_SKILL_ROOTS → single 'skills' entry under repo_root."""
    roots = _parse_skill_roots_env(repo_root=tmp_path, env={})
    assert roots == (tmp_path.joinpath("skills").resolve(),)


def test_parse_skill_roots_env_empty_string_uses_default(tmp_path: Path) -> None:
    """FSMC_SKILL_ROOTS='' (literal empty string) → treated as unset (D-23)."""
    roots = _parse_skill_roots_env(repo_root=tmp_path, env={"FSMC_SKILL_ROOTS": ""})
    assert roots == (tmp_path.joinpath("skills").resolve(),)


def test_parse_skill_roots_env_whitespace_only_uses_default(tmp_path: Path) -> None:
    """Whitespace-only env value → treated as unset."""
    roots = _parse_skill_roots_env(
        repo_root=tmp_path, env={"FSMC_SKILL_ROOTS": "   "}
    )
    assert roots == (tmp_path.joinpath("skills").resolve(),)


def test_parse_skill_roots_env_single_value(tmp_path: Path) -> None:
    roots = _parse_skill_roots_env(
        repo_root=tmp_path,
        env={"FSMC_SKILL_ROOTS": "tests/fixtures/skills"},
    )
    assert roots == (tmp_path.joinpath("tests/fixtures/skills").resolve(),)


def test_parse_skill_roots_env_colon_separated(tmp_path: Path) -> None:
    roots = _parse_skill_roots_env(
        repo_root=tmp_path, env={"FSMC_SKILL_ROOTS": "a:b:c"}
    )
    assert roots == (
        tmp_path.joinpath("a").resolve(),
        tmp_path.joinpath("b").resolve(),
        tmp_path.joinpath("c").resolve(),
    )


def test_parse_skill_roots_env_drops_empty_segments(tmp_path: Path) -> None:
    """``a::b: :c`` drops the empty and whitespace-only segments."""
    roots = _parse_skill_roots_env(
        repo_root=tmp_path, env={"FSMC_SKILL_ROOTS": "a::b: :c"}
    )
    assert roots == (
        tmp_path.joinpath("a").resolve(),
        tmp_path.joinpath("b").resolve(),
        tmp_path.joinpath("c").resolve(),
    )


def test_parse_skill_roots_env_honors_absolute_segments(tmp_path: Path) -> None:
    """Absolute segments stay absolute; relative resolve against repo_root."""
    abs_dir = tmp_path / "abs_root"
    abs_dir.mkdir()
    roots = _parse_skill_roots_env(
        repo_root=tmp_path,
        env={"FSMC_SKILL_ROOTS": f"{abs_dir}:relpath"},
    )
    assert roots == (
        abs_dir.resolve(),
        tmp_path.joinpath("relpath").resolve(),
    )


# ---------------------------------------------------------------------------
# Lifespan — happy / fatal / immutability
# ---------------------------------------------------------------------------


async def test_lifespan_happy_path_yields_indexer_catalog(monkeypatch) -> None:
    """The yielded catalog is the EXACT frozen Catalog instance the indexer produced.

    Identity check (``is``), not equality — proves list_skills will never
    re-scan disk (INIT-04 / SC5).
    """
    _prime_auth(monkeypatch)
    skills = (_make_skill("alpha"), _make_skill("beta"))
    catalog_in = Catalog(skills=skills)
    result = IndexResult(catalog=catalog_in, errors=())
    _patch_indexer(monkeypatch, result)
    persist_log: list[dict[str, Any]] = []
    _patch_persist(monkeypatch, persist_log)

    async with app_lifespan(mcp) as lifespan_dict:
        assert "catalog" in lifespan_dict
        assert lifespan_dict["catalog"] is catalog_in  # identity, not equality
        assert lifespan_dict["catalog"].skills is skills
        assert "lock_mgr" in lifespan_dict
        assert "task_mgr" in lifespan_dict

    # persist_index ran once during startup.
    assert len(persist_log) == 1
    assert persist_log[0]["result_skill_count"] == 2


async def test_lifespan_duplicate_name_exits_3(monkeypatch, capsys) -> None:
    """DUPLICATE_NAME → SystemExit(3); both abs paths on stderr; persist ran first."""
    _prime_auth(monkeypatch)
    # Non-empty catalog so the empty-catalog guard does NOT pre-empt the dup guard.
    catalog_in = Catalog(skills=(_make_skill("gamma"),))
    abs_a = "/tmp/test-dup/alpha/SKILL.md"
    abs_b = "/tmp/test-dup/beta/SKILL.md"
    errs = (
        _make_dup_error("dup-skill", abs_a),
        _make_dup_error("dup-skill", abs_b),
    )
    result = IndexResult(catalog=catalog_in, errors=errs)
    _patch_indexer(monkeypatch, result)
    persist_log: list[dict[str, Any]] = []
    _patch_persist(monkeypatch, persist_log)

    with pytest.raises(SystemExit) as exc_info:
        async with app_lifespan(mcp) as _lifespan_dict:
            pytest.fail("lifespan should have exited on duplicate-name fatal")
    assert exc_info.value.code == 3

    captured = capsys.readouterr()
    # DUPLICATE_NAME token must appear literally so operators can grep logs.
    assert "DUPLICATE_NAME" in captured.err
    # Both absolute paths must be visible to the operator without scrolling.
    assert abs_a in captured.err
    assert abs_b in captured.err
    # persist_index ran exactly once and ran BEFORE the SystemExit fired.
    assert len(persist_log) == 1
    assert persist_log[0]["result_error_count"] == 2


async def test_lifespan_empty_catalog_exits_4(monkeypatch, capsys) -> None:
    """Empty Catalog → SystemExit(4) with per-code Counter summary on stderr."""
    _prime_auth(monkeypatch)
    catalog_in = Catalog(skills=())
    errs = (
        IxErr(
            path="/tmp/test-empty/missing-name/SKILL.md",
            error_code=IndexErrorCode.MISSING_NAME,
            message="missing name",
        ),
        IxErr(
            path="/tmp/test-empty/bad-yaml/SKILL.md",
            error_code=IndexErrorCode.INVALID_YAML,
            message="bad yaml",
            line=3,
        ),
        IxErr(
            path="/tmp/test-empty/missing-name-2/SKILL.md",
            error_code=IndexErrorCode.MISSING_NAME,
            message="missing name 2",
        ),
    )
    result = IndexResult(catalog=catalog_in, errors=errs)
    _patch_indexer(monkeypatch, result)
    persist_log: list[dict[str, Any]] = []
    _patch_persist(monkeypatch, persist_log)

    with pytest.raises(SystemExit) as exc_info:
        async with app_lifespan(mcp) as _lifespan_dict:
            pytest.fail("lifespan should have exited on empty-catalog fatal")
    assert exc_info.value.code == 4

    captured = capsys.readouterr()
    assert "NO VALID SKILLS" in captured.err
    # Counter summary mentions every error code that was present, with counts.
    assert "MISSING_NAME" in captured.err
    assert "INVALID_YAML" in captured.err
    # The two MISSING_NAME errors should show count of 2 in the Counter dict.
    assert "'MISSING_NAME': 2" in captured.err
    assert "'INVALID_YAML': 1" in captured.err
    # persist_index ran exactly once BEFORE the exit.
    assert len(persist_log) == 1


async def test_lifespan_persist_runs_before_fatal_exits(monkeypatch) -> None:
    """The mocked persist_index call is recorded BEFORE SystemExit fires."""
    _prime_auth(monkeypatch)
    catalog_in = Catalog(skills=(_make_skill("gamma"),))
    errs = (
        _make_dup_error("dup-skill", "/tmp/persist-order/a/SKILL.md"),
        _make_dup_error("dup-skill", "/tmp/persist-order/b/SKILL.md"),
    )
    result = IndexResult(catalog=catalog_in, errors=errs)
    _patch_indexer(monkeypatch, result)
    persist_log: list[dict[str, Any]] = []
    _patch_persist(monkeypatch, persist_log)

    pre_attempt = time.monotonic()
    with pytest.raises(SystemExit):
        async with app_lifespan(mcp):
            pass
    post_attempt = time.monotonic()

    # persist_index ran once, and its timestamp lies inside the
    # [pre_attempt, post_attempt] window — i.e. BEFORE SystemExit.
    assert len(persist_log) == 1
    persist_ts = persist_log[0]["ts"]
    assert pre_attempt <= persist_ts <= post_attempt


async def test_lifespan_auth_smoke_test_still_runs_first(monkeypatch) -> None:
    """No auth env vars → SystemExit(2); the indexer is NEVER called."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    indexer_calls: list[tuple] = []
    # Indexer fake should be unreached — but install it so we can prove it.
    _patch_indexer(
        monkeypatch,
        IndexResult(catalog=Catalog(skills=(_make_skill("never-called"),)), errors=()),
        calls=indexer_calls,
    )

    with pytest.raises(SystemExit) as exc_info:
        async with app_lifespan(mcp):
            pass
    assert exc_info.value.code == 2
    # Auth gate fired BEFORE indexer — the fake was never invoked.
    assert indexer_calls == []


async def test_lifespan_catalog_is_frozen(monkeypatch) -> None:
    """The yielded Catalog is frozen — mutation raises FrozenInstanceError."""
    _prime_auth(monkeypatch)
    skills = (_make_skill("alpha"),)
    catalog_in = Catalog(skills=skills)
    result = IndexResult(catalog=catalog_in, errors=())
    _patch_indexer(monkeypatch, result)
    persist_log: list[dict[str, Any]] = []
    _patch_persist(monkeypatch, persist_log)

    async with app_lifespan(mcp) as lifespan_dict:
        cat = lifespan_dict["catalog"]
        with pytest.raises(dataclasses.FrozenInstanceError):
            cat.skills = ()  # type: ignore[misc]
        # Skill entries themselves are also frozen.
        with pytest.raises(dataclasses.FrozenInstanceError):
            cat.skills[0].name = "mutated"  # type: ignore[misc]
