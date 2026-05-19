"""Contract test (SPEC-09). The single most load-bearing CI guard:
fails if any OpenSpec example diverges from its declared JSON Schema,
OR if any spec is missing required Schemas/Examples sections, OR if
the spec.md format drifts from the parser's expectations.

Per PLAN 00-02 DECISION-LOG: Option A confirmed - schemas + examples
live as fenced ```json blocks inside ``## Schemas`` / ``## Examples`` H2
sections of each ``openspec/specs/<capability>/spec.md`` file. No sidecar
JSON files exist; this test parses the markdown directly.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import jsonschema
import pytest

# REPO_ROOT works whether pytest is invoked from repo root or from tests/
REPO_ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = REPO_ROOT / "openspec" / "specs"


# Match an H3 heading followed by a ```json fenced code block.
# Captures the section name and the JSON body. Permissive on whitespace
# between heading and fence (Pitfall 4).
_SECTION_JSON = re.compile(
    r"^###\s+(?P<section>[A-Za-z][A-Za-z0-9 _\-()]*?)\s*$"
    r"(?:\s*\n)+```json\s*\n"
    r"(?P<body>.*?)"
    r"\n```",
    re.MULTILINE | re.DOTALL,
)


def _parse_spec(spec_path: Path) -> dict[str, dict[str, Any]]:
    """Extract schemas and examples from a spec.md file.

    Returns a dict shaped::

        {
            "schemas": {"Request": {...}, "Response (success)": {...}, ...},
            "examples": {"Happy path": {"request": ..., "response": ...}, ...},
        }
    """
    text = spec_path.read_text(encoding="utf-8")

    schemas: dict[str, Any] = {}
    examples: dict[str, Any] = {}

    schemas_match = re.search(
        r"^##\s+Schemas\s*$(.+?)(?=^##\s+|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if schemas_match:
        for m in _SECTION_JSON.finditer(schemas_match.group(1)):
            schemas[m.group("section").strip()] = json.loads(m.group("body"))

    examples_match = re.search(
        r"^##\s+Examples\s*$(.+?)(?=^##\s+|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if examples_match:
        for m in _SECTION_JSON.finditer(examples_match.group(1)):
            examples[m.group("section").strip()] = json.loads(m.group("body"))

    return {"schemas": schemas, "examples": examples}


def _collect_specs() -> list[Path]:
    return sorted(SPECS_DIR.glob("*/spec.md"))


def _select_error_schema(
    schemas: dict[str, Any], response_payload: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Pick the error schema that the given example response payload satisfies.

    Strategy:
      1. Collect all schemas whose key contains "error" (case-insensitive).
      2. Try ``jsonschema.validate(payload, schema)`` against each in order.
      3. Return the first (key, schema) that validates.
      4. If none match, raise AssertionError with a clear message listing
         every error schema that was tried and why it rejected the payload.

    Does NOT silently fall back to the success schema - that would defeat
    the contract guard's purpose.
    """
    error_schemas = {k: v for k, v in schemas.items() if "error" in k.lower()}
    if not error_schemas:
        raise AssertionError(
            f"Example response has isError=true but no schema with 'error' "
            f"in its key was declared in ## Schemas. Available keys: "
            f"{sorted(schemas.keys())!r}"
        )

    rejection_log: list[str] = []
    for key, schema in error_schemas.items():
        try:
            jsonschema.validate(response_payload, schema)
            return key, schema
        except jsonschema.ValidationError as exc:
            # Compact one-line summary of why this schema rejected the payload.
            rejection_log.append(f"  - {key!r}: {exc.message}")

    raise AssertionError(
        "Example response has isError=true but did not validate against any "
        "declared error schema. Tried:\n" + "\n".join(rejection_log)
    )


@pytest.mark.contract
@pytest.mark.parametrize(
    "spec_path",
    _collect_specs(),
    ids=lambda p: p.parent.name,
)
def test_examples_validate_against_schemas(spec_path: Path) -> None:
    """For every OpenSpec capability, parse its spec.md, then validate
    each example's request + response against the declared schemas.

    Each spec.md => N examples => N validation pairs. A single failure
    pinpoints the offending capability and example.
    """
    parsed = _parse_spec(spec_path)
    schemas = parsed["schemas"]
    examples = parsed["examples"]

    assert schemas, f"{spec_path}: no Schemas section parsed"
    assert examples, f"{spec_path}: no Examples section parsed"

    request_schema = schemas.get("Request")
    response_success = schemas.get("Response (success)")

    assert request_schema is not None, (
        f"{spec_path}: missing required schema 'Request' in ## Schemas"
    )

    for ex_name, payload in examples.items():
        # Every example MUST have a request and a response.
        assert "request" in payload, (
            f"{spec_path}::{ex_name}: example missing 'request' key"
        )
        assert "response" in payload, (
            f"{spec_path}::{ex_name}: example missing 'response' key"
        )

        # Validate the request against the Request schema.
        jsonschema.validate(payload["request"], request_schema)

        # Pick the right response schema and validate.
        response = payload["response"]
        is_error = isinstance(response, dict) and response.get("isError") is True

        if is_error:
            # Use the smart selector; will raise AssertionError if no
            # error schema accepts the payload. Does NOT fall back to
            # the success schema.
            _select_error_schema(schemas, response)
        else:
            assert response_success is not None, (
                f"{spec_path}::{ex_name}: non-error example but no "
                f"'Response (success)' schema declared"
            )
            jsonschema.validate(response, response_success)


# ---------------------------------------------------------------------------
# Parser-of-parser test (Pitfall 4)
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_parser_handles_clean_markdown(tmp_path: Path) -> None:
    """Sanity check the spec.md regex parser against an in-memory fixture.

    Catches regex regressions BEFORE they cause the parametrized contract
    tests to spuriously skip (because they'd find zero JSON blocks and
    fail the 'schemas/examples non-empty' assertions).
    """
    fixture = tmp_path / "spec.md"
    fixture.write_text(
        "# fake Specification\n"
        "\n"
        "## Purpose\n"
        "\n"
        "Sample.\n"
        "\n"
        "## Requirements\n"
        "\n"
        "### Requirement: foo\n"
        "The system SHALL foo.\n"
        "\n"
        "#### Scenario: bar\n"
        "- GIVEN x\n"
        "- WHEN y\n"
        "- THEN z\n"
        "\n"
        "## Schemas\n"
        "\n"
        "### Request\n"
        "\n"
        "```json\n"
        '{"type": "object", "properties": {"x": {"type": "integer"}}}\n'
        "```\n"
        "\n"
        "### Response (success)\n"
        "\n"
        "```json\n"
        '{"type": "object", "properties": {"ok": {"type": "boolean"}}}\n'
        "```\n"
        "\n"
        "## Examples\n"
        "\n"
        "### Happy\n"
        "\n"
        "```json\n"
        '{"request": {"x": 1}, "response": {"ok": true}}\n'
        "```\n",
        encoding="utf-8",
    )

    parsed = _parse_spec(fixture)
    assert set(parsed["schemas"].keys()) == {"Request", "Response (success)"}
    assert parsed["schemas"]["Request"]["type"] == "object"
    assert "Happy" in parsed["examples"]
    assert parsed["examples"]["Happy"]["request"] == {"x": 1}
    assert parsed["examples"]["Happy"]["response"] == {"ok": True}
