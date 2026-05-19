"""Shared FastMCP Client result-shape helpers for Walking Skeleton tests.

Attribute names verified empirically in 01-01-SUMMARY.md §A4 / §A5:

* ``fastmcp.client.client.CallToolResult`` exposes:
    - ``content``           list[ContentBlock]
    - ``structured_content`` dict | None
    - ``meta``              dict | None        ← server's ``_meta`` (D-23 error meta)
    - ``data``              Any                ← deserialized return value when the
                                                 tool returns a JSON-compatible body
    - ``is_error``          bool               ← True only via ``ErrorToolResult.to_mcp_result``

Happy-path tools (``list_skills``, ``create_task``, ``get_task_status``,
``get_task_result``) return a plain ``dict``; FastMCP serializes it into
``structured_content`` and ``data`` on the client side. Error responses route
through ``ErrorToolResult`` (§A4 PIVOT) → ``is_error=True`` + ``meta={...}``.
"""
from __future__ import annotations

from typing import Any


def extract_data(result: Any) -> dict:
    """Return the structured payload dict from a FastMCP Client call_tool result.

    Prefers ``result.data`` (FastMCP's deserialized return value); falls back to
    ``result.structured_content`` (the raw MCP structuredContent) if ``data`` is
    None, and finally to ``{}`` if neither is populated. Returning a dict (vs
    ``None``) lets tests do ``.get(...)`` lookups unconditionally.
    """
    data = getattr(result, "data", None)
    if data is not None:
        return data if isinstance(data, dict) else {"value": data}
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    return {}


def extract_meta(result: Any) -> dict:
    """Return ``_meta`` dict (server-side ``ErrorToolResult.meta``) or ``{}``."""
    meta = getattr(result, "meta", None)
    return meta if isinstance(meta, dict) else {}


def is_error(result: Any) -> bool:
    """Return True iff the call_tool result was produced by ``ErrorToolResult``."""
    return bool(getattr(result, "is_error", False))
