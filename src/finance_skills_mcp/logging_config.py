"""Structured-logging configuration (D-36, D-37, D-38, D-39).

This module owns the structlog wiring for the server. It exposes four
symbols:

- ``configure_logging(level, stream)`` — idempotent global setup that wires
  structlog with a JSONRenderer terminal processor and a contextvars merge
  processor. Called exactly once from ``server.main()`` (D-36).
- ``bind_task_context(task_id, tool_name, skill_ids)`` — binds the three
  mandatory D-38 fields into the structlog contextvars scope so every
  subsequent log line carries them automatically.
- ``clear_task_context()`` — releases the contextvars scope at the end of
  a task lifecycle (called from ``TaskManager.create()``'s finally block).
- ``task_logger(log_path)`` — opens an append-mode file handle at
  ``log_path`` and returns ``(logger, file_handle)``. The caller is
  responsible for closing the handle (typically in a finally block) and
  for invoking this helper via ``anyio.to_thread.run_sync(...)`` from
  inside any ``async def`` body (D-22 / EXEC-07).

The module never opens a file at import time; all I/O is initiated by the
caller, which lets us preserve the D-22 async-open guard without forcing
the entire module into async semantics.

Reference: 03-CONTEXT.md D-36..D-40; 01-CONTEXT.md D-22.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import IO, Any

import structlog

__all__ = [
    "configure_logging",
    "bind_task_context",
    "clear_task_context",
    "task_logger",
]


# Module-level guard ensures repeated calls to ``configure_logging`` are
# no-ops (idempotent — D-36). Tests may flip this back to ``False`` via
# ``_reset_for_tests()`` if they need to re-exercise the wiring path.
_CONFIGURED: bool = False


def _build_processors() -> list[Any]:
    """The canonical processor pipeline shared by the global and per-task loggers.

    Order is significant:

    1. ``merge_contextvars`` — merges any ``bind_contextvars`` payload into
       the event dict so D-38 fields flow into every line.
    2. ``add_log_level`` — adds the ``level`` field (matches stdlib
       ``logging`` semantics; operators can grep on it).
    3. ``TimeStamper(fmt="iso", utc=True)`` — adds the ``timestamp`` field
       in ISO-8601 UTC.
    4. ``format_exc_info`` — promotes ``exc_info`` to a ``exception`` field
       so JSONRenderer can serialise it.
    5. ``JSONRenderer(sort_keys=True)`` — terminal processor; serialises
       to one JSON object per log line. ``sort_keys`` makes the output
       byte-deterministic which the integration tests assert against.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(sort_keys=True),
    ]


def configure_logging(
    level: int = logging.INFO,
    stream: IO[str] | None = None,
) -> None:
    """Configure structlog globally. Idempotent — repeat calls are no-ops.

    The default ``level`` is ``logging.INFO`` per D-36. The default
    ``stream`` is ``sys.stderr`` per D-25 (stdout is reserved for MCP
    JSON-RPC traffic in stdio mode).

    Args:
        level: minimum log level to emit; lower-severity calls are
            silently discarded by the filtering bound logger.
        stream: the file-like text stream the global logger writes to.
            Defaults to ``sys.stderr``.

    Note:
        Idempotency is enforced by a module-level ``_CONFIGURED`` flag.
        This matters because in-memory tests construct the FastMCP server
        repeatedly across a session; calling ``configure_logging`` on
        every test would otherwise leak state.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    if stream is None:
        stream = sys.stderr

    structlog.configure(
        processors=_build_processors(),
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=stream),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def _reset_for_tests() -> None:
    """Internal: drop the global-configured flag so tests can re-exercise.

    NOT part of the public API. Use sparingly — production code never
    needs to reset the logger configuration.
    """
    global _CONFIGURED
    _CONFIGURED = False
    structlog.reset_defaults()


def bind_task_context(
    task_id: str, tool_name: str, skill_ids: list[str]
) -> None:
    """Bind the three mandatory D-38 fields into the structlog contextvars scope.

    Every log call made on any structlog logger after this binding will
    carry ``task_id``, ``tool_name``, and ``skill_ids`` automatically via
    the ``merge_contextvars`` processor. Call ``clear_task_context()`` at
    the end of the task lifecycle so the next request starts clean.

    Args:
        task_id: the server-generated task identifier (validated upstream
            by ``task_store.validate_task_id``).
        tool_name: the MCP tool name (currently always ``"create_task"`` —
            the only tool that spawns a task lifecycle).
        skill_ids: the list of skill ID strings the caller requested.
            Stored as a list (a fresh copy) so later mutations by the
            caller do not retroactively change the bound value.
    """
    structlog.contextvars.bind_contextvars(
        task_id=task_id,
        tool_name=tool_name,
        skill_ids=list(skill_ids),
    )


def clear_task_context() -> None:
    """Release the contextvars scope previously seeded by ``bind_task_context``.

    Called from ``TaskManager.create()``'s finally block so the next
    incoming request starts with a clean per-task context — no stale
    ``task_id`` bleeds across tasks.
    """
    structlog.contextvars.clear_contextvars()


def task_logger(log_path: Path) -> tuple[Any, IO[str]]:
    """Open ``log_path`` in append mode and return a structlog logger + handle.

    Returns:
        A ``(logger, file_handle)`` pair. The caller MUST close the
        handle (typically in a finally block) once the task lifecycle
        completes. The logger writes JSON-per-line to the handle and
        reuses the canonical processor pipeline from
        ``_build_processors()`` so per-task lines are shape-identical to
        global stderr lines.

    Important:
        This function is intentionally synchronous. It performs a
        blocking ``Path.open`` call which MUST be invoked from an async
        site via ``anyio.to_thread.run_sync(task_logger, log_path)`` to
        respect the D-22 async-open guard. The CI check
        ``scripts/ci/forbid_async_open.py`` validates this discipline
        across ``src/``.

    Implementation notes:

    - ``buffering=1`` makes the file line-buffered so each log line is
      flushed to disk on the trailing newline — operators can ``tail -f``
      the file in real time without waiting for a buffer flush.
    - ``structlog.PrintLoggerFactory`` is reused (not the stdlib logging
      bridge) because the global stderr logger already uses it; the
      per-task file logger therefore produces byte-identical output for
      the same event dict, just routed to a different sink.
    - The returned logger is independent of the global structlog config
      (it wraps a fresh ``BoundLoggerBase`` instance) so calling this
      function before ``configure_logging`` still produces well-formed
      JSON output. This is critical for testing the per-task file path
      in isolation.

    Args:
        log_path: absolute path to the per-task ``server.jsonl`` file.
            The parent directory MUST already exist (caller's
            responsibility — ``create_task_dirs`` is the canonical
            preparer in production).
    """
    # Line-buffered append-mode handle. UTF-8 covers the full Unicode
    # surface so any string a structlog processor produces is encodable.
    fh = log_path.open("a", encoding="utf-8", buffering=1)
    logger = structlog.wrap_logger(
        structlog.PrintLogger(file=fh),
        processors=_build_processors(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    return logger, fh
