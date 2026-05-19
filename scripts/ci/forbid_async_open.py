#!/usr/bin/env python3
"""D-22 / Pitfall 1 — forbid bare ``open(...)`` calls inside ``async def`` in ``src/``.

Why: Python's built-in ``open()`` is synchronous; calling it directly from
inside an ``async def`` body blocks the event loop for the duration of the
filesystem syscall. In a single-threaded asyncio runtime that is a hard
hygiene violation — every other coroutine (status polls, BUSY checks,
heartbeat refresh) waits behind that one disk read.

The fix in production code is to wrap blocking I/O in
``anyio.to_thread.run_sync(open, ...)`` — the runner offloads the call to a
worker thread, the event loop stays responsive (EXEC-07 / 01-CONTEXT.md
D-22).

This script walks ``src/`` (default; first CLI arg overrides), parses each
``*.py`` via ``ast``, and reports any ``Call`` node whose ``func`` is
``Name(id="open")`` lexically reachable from an ``AsyncFunctionDef`` body —
unless the call is itself an argument to ``anyio.to_thread.run_sync(...)``
or a similar known thread-runner.

Exit code: 0 if clean, 1 if any violation is found.

Reference: 01-04-PLAN.md Task 1 (async-open guard); 01-CONTEXT.md D-22;
01-01-SUMMARY.md deviation #2 (recovered from a near-miss of this pattern
inside ``LockManager.startup_recovery``).
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# Calls that legitimately wrap a blocking function — the inner ``open`` is
# fine because the runner offloads it to a worker thread.
THREAD_RUNNER_QUALNAMES = {
    # Canonical anyio API used throughout the codebase.
    ("anyio", "to_thread", "run_sync"),
    # Less common but acceptable equivalents — keep the allowlist tight.
    ("asyncio", "to_thread"),
}


def _qualname(node: ast.AST) -> tuple[str, ...] | None:
    """Return a dotted-attribute qualname tuple, or None if the call target
    is not a plain attribute chain (e.g. ``foo.bar.baz``)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return None


def _is_thread_runner_call(call: ast.Call) -> bool:
    qn = _qualname(call.func)
    return qn in THREAD_RUNNER_QUALNAMES


def _is_bare_open_call(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Name) and call.func.id == "open"


class AsyncOpenVisitor(ast.NodeVisitor):
    """Records every ``open(...)`` Call lexically inside an ``async def``,
    unless the open is passed as a positional argument to a known thread
    runner (e.g. ``anyio.to_thread.run_sync(open, ...)``)."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.async_depth = 0
        # When the visitor enters an ``anyio.to_thread.run_sync(open, ...)``
        # call, the inner ``Name(id="open")`` is a *reference* to the
        # function, not an inline ``open(...)`` call. To be safe we still
        # walk the call's args, but the ``Call`` check below uses
        # ``isinstance(node.func, ast.Name)`` — a bare Name reference is
        # NOT a Call, so it won't be flagged. No special handling needed.
        self.violations: list[tuple[int, int, str]] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.async_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self.async_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # A nested *sync* function inside an async def is a synchronous
        # context — open() inside it does NOT block the event loop directly
        # (the runner will only call it via to_thread.run_sync). Reset the
        # async-depth for the duration of the nested sync function.
        saved = self.async_depth
        self.async_depth = 0
        try:
            self.generic_visit(node)
        finally:
            self.async_depth = saved

    def visit_Lambda(self, node: ast.Lambda) -> None:
        # Same logic as nested sync function — lambdas are sync bodies.
        saved = self.async_depth
        self.async_depth = 0
        try:
            self.generic_visit(node)
        finally:
            self.async_depth = saved

    def visit_Call(self, node: ast.Call) -> None:
        if self.async_depth > 0 and _is_bare_open_call(node):
            # We're inside an async def, and this is a literal ``open(...)``
            # call. Permit it only if the call is the first positional
            # argument to a thread runner — but Python's AST stores that
            # case as ``Call(func=anyio...run_sync, args=[Name("open"), ...])``
            # where the inner is a Name reference, not a Call. So if we
            # reached here it really is a bare ``open(...)``.
            line = node.lineno
            col = node.col_offset
            self.violations.append(
                (line, col, f"bare open() call inside async def")
            )
        # Special-case the wrapped form for thoroughness — if a developer
        # writes ``anyio.to_thread.run_sync(lambda: open(...))`` the open
        # call is inside a sync lambda; visit_Lambda already handles that.
        self.generic_visit(node)


def check_file(path: Path) -> list[tuple[Path, int, int, str]]:
    """Return list of (path, line, col, message) violations in this file."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [(path, exc.lineno or 0, exc.offset or 0, f"SyntaxError: {exc.msg}")]
    visitor = AsyncOpenVisitor(filename=str(path))
    visitor.visit(tree)
    return [(path, line, col, msg) for line, col, msg in visitor.violations]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forbid bare open() inside async def in src/ (D-22)."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default="src",
        help="Directory to scan recursively (default: src).",
    )
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"::error::{root} is not a directory", file=sys.stderr)
        return 1

    all_violations: list[tuple[Path, int, int, str]] = []
    for py_file in sorted(root.rglob("*.py")):
        all_violations.extend(check_file(py_file))

    if not all_violations:
        print(f"OK: no bare open() found inside async def in {root}/")
        return 0

    for path, line, col, msg in all_violations:
        print(
            f"::error file={path},line={line},col={col}::"
            f"D-22 violation in {path}:{line}:{col}: {msg} — "
            f"wrap in anyio.to_thread.run_sync(...) per EXEC-07.",
            file=sys.stderr,
        )
    print(
        f"FAIL: {len(all_violations)} bare open() call(s) inside async def in {root}/",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
