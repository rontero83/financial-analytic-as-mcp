"""Structured-error wire shape helpers (D-23, D-24).

Phase 2 (D-31) appends ``IndexErrorCode`` — typed codes the indexer emits
into ``.skills-index/errors.json``. The enum lives here because this is
already the typed-error module for the project; the indexer imports
``IndexErrorCode`` from ``finance_skills_mcp.errors``.

§A4 of 01-01-SUMMARY.md captures the empirical pivot: FastMCP 3.3.1's
``ToolResult.to_mcp_result()`` produces a ``CallToolResult`` only when ``meta``
is set, and that helper does NOT set ``isError``. Raising ``ToolError(msg)``
DROPS custom ``_meta``. The only clean path to ``{isError: true, _meta: {...}}``
over the wire is a ``ToolResult`` subclass that overrides ``to_mcp_result()``.

This module owns that subclass and a tiny factory for each error code in
``specs/create-task/spec.md`` / ``specs/get-task-result/spec.md``.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from fastmcp.tools.tool import ToolResult
from mcp.types import CallToolResult, TextContent


class IndexErrorCode(str, Enum):
    """Typed error codes emitted by ``skill_indexer.index()`` (D-31).

    Members are string-valued so ``json.dumps(IndexErrorCode.MISSING_NAME)``
    yields the bare token ``"MISSING_NAME"`` — the on-disk
    ``.skills-index/errors.json`` therefore uses bare strings, not the Python
    repr.

    The 8 D-31 codes plus ``INVALID_PATH`` (Phase 2 02-PLAN-CHECK M-2 fix —
    distinguishes a symlink-escape failure from a name-format failure so the
    operator's ``errors.json`` consumers can branch cleanly).

    All members EXCEPT ``UNKNOWN_FIELD`` are hard errors — the offending
    skill is skipped. ``UNKNOWN_FIELD`` is warning severity — the skill is
    still indexed (D-28).
    """

    MISSING_NAME = "MISSING_NAME"
    MISSING_DESCRIPTION = "MISSING_DESCRIPTION"
    INVALID_NAME = "INVALID_NAME"
    INVALID_YAML = "INVALID_YAML"
    EMPTY_FILE = "EMPTY_FILE"
    ENCODING_ERROR = "ENCODING_ERROR"
    DUPLICATE_NAME = "DUPLICATE_NAME"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    INVALID_PATH = "INVALID_PATH"

    def is_warning(self) -> bool:
        """True only for ``UNKNOWN_FIELD`` (D-28 — warning severity)."""
        return self is IndexErrorCode.UNKNOWN_FIELD


class ErrorToolResult(ToolResult):
    """ToolResult subclass that emits ``isError: true`` over the MCP wire.

    Use this for every D-23 / D-24 error code (BUSY, UNKNOWN_SKILL,
    INVALID_PROMPT, TASK_NOT_FOUND, TASK_NOT_TERMINAL, STORAGE_ERROR,
    SERVER_RUNTIME_ERROR). The ``meta`` payload carries the per-code
    structured fields (``error_code``, ``inflight_task_id``, ``started_at``,
    ``task_id``, ``current_status`` — schema-dependent).
    """

    def to_mcp_result(self) -> CallToolResult:  # type: ignore[override]
        return CallToolResult(
            content=self.content or [],
            structuredContent=self.structured_content,
            isError=True,
            _meta=self.meta or {},  # type: ignore[call-arg]
        )


def busy_error(inflight_task_id: str, started_at: str) -> ErrorToolResult:
    """D-23 BUSY: another task is in flight. Carries the in-flight task id."""
    return ErrorToolResult(
        content=[TextContent(type="text", text="Task already in flight")],
        meta={
            "inflight_task_id": inflight_task_id,
            "started_at": started_at,
        },
    )


def validation_error(error_code: str, message: str, **extra: Any) -> ErrorToolResult:
    """D-23 validation errors (UNKNOWN_SKILL, INVALID_PROMPT, STORAGE_ERROR)."""
    meta: dict[str, Any] = {"error_code": error_code}
    meta.update(extra)
    return ErrorToolResult(
        content=[TextContent(type="text", text=message)],
        meta=meta,
    )


def task_not_found(task_id: str) -> ErrorToolResult:
    """D-23 TASK_NOT_FOUND: ``get_task_status`` / ``get_task_result``."""
    return ErrorToolResult(
        content=[TextContent(type="text", text=f"Task not found: {task_id}")],
        meta={"error_code": "TASK_NOT_FOUND", "task_id": task_id},
    )


def task_not_terminal(task_id: str, current_status: str) -> ErrorToolResult:
    """D-24 TASK_NOT_TERMINAL: ``get_task_result`` on a non-terminal task.

    Does NOT block — clients are expected to poll ``get_task_status``.
    """
    return ErrorToolResult(
        content=[
            TextContent(
                type="text",
                text=f"Task {task_id} is not in a terminal state",
            )
        ],
        meta={
            "error_code": "TASK_NOT_TERMINAL",
            "task_id": task_id,
            "current_status": current_status,
        },
    )


def server_runtime_error(message: str, **extra: Any) -> ErrorToolResult:
    """D-23 SERVER_RUNTIME_ERROR: catch-all for unhandled server-side failures."""
    meta: dict[str, Any] = {"error_code": "SERVER_RUNTIME_ERROR"}
    meta.update(extra)
    return ErrorToolResult(
        content=[TextContent(type="text", text=message)],
        meta=meta,
    )
