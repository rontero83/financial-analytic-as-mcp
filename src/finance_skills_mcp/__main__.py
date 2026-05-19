"""Process entry point for ``python -m finance_skills_mcp`` (D-02).

Delegates to ``server.main`` via ``anyio.run`` so the stdio MCP server is
driven on the project-standard async runtime.
"""
from __future__ import annotations

import anyio

from finance_skills_mcp.server import main

if __name__ == "__main__":
    anyio.run(main)
