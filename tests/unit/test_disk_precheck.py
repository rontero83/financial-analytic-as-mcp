"""Pure-unit coverage of ``_parse_free_space_mb_env`` (D-41).

Six behaviors enumerated in 03-02-PLAN.md Task 1 <behavior>:

1. Default (env unset) -> 100.
2. Default fallback for empty / whitespace-only string -> 100.
3. Valid positive integer parsed verbatim.
4. Non-integer string raises ``ValueError`` (incl. float-string "100.5").
5. Zero or negative raises ``ValueError`` with operator-actionable message.
6. ``env=None`` honors ``os.environ`` (via monkeypatch); explicit ``env=``
   wins over ``os.environ`` so test injection is faithful (mirrors the
   established ``_parse_skill_roots_env`` DI contract).

Tests import the helper directly from the server module; no FastMCP
machinery is invoked here -- these are pure parser tests.
"""
from __future__ import annotations

import pytest

from finance_skills_mcp.server import _parse_free_space_mb_env


# ---------------------------------------------------------------------------
# Test 1 — default when env unset
# ---------------------------------------------------------------------------


def test_default_value_is_100_when_env_unset():
    """Empty mapping (env unset) -> default threshold 100 MB (D-41)."""
    assert _parse_free_space_mb_env(env={}) == 100


# ---------------------------------------------------------------------------
# Test 2 — default fallback for blank / whitespace string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   ", "\t", "\n", "  \t\n  "])
def test_default_value_is_100_when_env_empty_string(raw):
    """Whitespace-only env values fall through to the default (D-41)."""
    assert _parse_free_space_mb_env(env={"FSMC_FREE_SPACE_MB": raw}) == 100


# ---------------------------------------------------------------------------
# Test 3 — valid positive integer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", 1),
        ("100", 100),
        ("250", 250),
        ("999999", 999999),
        ("  50  ", 50),  # int() tolerates surrounding whitespace
    ],
)
def test_valid_positive_int_is_parsed(raw, expected):
    """Positive integers (with optional surrounding whitespace) parse verbatim."""
    assert _parse_free_space_mb_env(env={"FSMC_FREE_SPACE_MB": raw}) == expected


# ---------------------------------------------------------------------------
# Test 4 — non-integer raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["abc", "100.5", "100MB", "1e3", "0x64", "0b10"])
def test_non_integer_raises_valueerror(bad):
    """Non-integer values (including floats, suffixes, alternate bases) reject.

    The message MUST name BOTH the env var and the offending raw value so
    operators can copy-paste from the stderr line into their shell config.
    """
    with pytest.raises(ValueError) as exc_info:
        _parse_free_space_mb_env(env={"FSMC_FREE_SPACE_MB": bad})
    msg = str(exc_info.value)
    assert "FSMC_FREE_SPACE_MB" in msg, (
        f"error message must name the env var; got: {msg!r}"
    )
    assert bad in msg, (
        f"error message must echo the offending value {bad!r}; got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Test 5 — zero or negative raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["0", "-1", "-100", "  -50  "])
def test_zero_or_negative_raises_valueerror(bad):
    """Zero and negatives are rejected; message instructs to use positives."""
    with pytest.raises(ValueError) as exc_info:
        _parse_free_space_mb_env(env={"FSMC_FREE_SPACE_MB": bad})
    msg = str(exc_info.value)
    assert "positive integer" in msg.lower(), (
        f"error message must instruct the operator that a positive integer "
        f"is required; got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Test 6 — env=None honors os.environ; explicit env= overrides it
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Test 7 — WR-03: non-canonical positive-integer forms are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "1_000",     # underscored digit grouping — Python int() accepts,
                     # parser rejects per README contract.
        "0100",      # leading zero — shell operators expecting octal would
                     # be surprised that Python 3 returns 100, not 64.
        "+100",      # explicit positive sign — semantically noise.
        "  +50  ",   # explicit positive sign with surrounding whitespace.
        "01",        # leading zero on small value.
    ],
)
def test_non_canonical_positive_integer_rejected(bad):
    """WR-03: even values Python's ``int()`` would accept are rejected when
    they fall outside the documented canonical form."""
    with pytest.raises(ValueError) as exc_info:
        _parse_free_space_mb_env(env={"FSMC_FREE_SPACE_MB": bad})
    msg = str(exc_info.value)
    assert "FSMC_FREE_SPACE_MB" in msg, (
        f"error message must name the env var; got: {msg!r}"
    )
    assert "canonical" in msg.lower(), (
        f"error message must explain the canonical-form requirement; "
        f"got: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Test 8 — env=None honors os.environ; explicit env= overrides it
# ---------------------------------------------------------------------------


def test_helper_never_touches_os_environ(monkeypatch):
    """Test-injection contract: explicit env= ALWAYS wins over os.environ.

    Mirrors the _parse_skill_roots_env DI seam — when the caller passes
    env=None we read from the live process environment; when the caller
    passes an explicit Mapping we read ONLY from it (so monkeypatched
    os.environ values do not leak into test cases that supply env=).
    """
    monkeypatch.setenv("FSMC_FREE_SPACE_MB", "200")
    # env=None -> falls back to os.environ
    assert _parse_free_space_mb_env(env=None) == 200
    # explicit env= overrides os.environ entirely
    assert _parse_free_space_mb_env(env={"FSMC_FREE_SPACE_MB": "50"}) == 50
    # explicit empty env= (no key) also falls through to the DEFAULT, not
    # to os.environ — the injected mapping is the source of truth.
    assert _parse_free_space_mb_env(env={}) == 100
