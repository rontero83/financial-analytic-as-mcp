# Finance Skills - Claude Code Guidance

This guide covers the 3 production-ready finance skills and their Python automation tools.

## Finance Skills Overview

**Available Skills:**
1. **financial-analyst/** - Financial statement analysis, ratio analysis, DCF valuation, budgeting, forecasting (4 Python tools)
2. **saas-metrics-coach/** - SaaS financial health: ARR, MRR, churn, CAC, LTV, NRR, Quick Ratio, 12-month projections (3 Python tools)
3. **business-investment-advisor/** - Investment thesis evaluation, ROI modeling, capital allocation guidance

**Total Tools:** 7 Python automation tools, 5 knowledge bases, 6 templates

**Commands:** 2 (`/financial-health`, `/saas-health`)

## Python Automation Tools

### 1. Ratio Calculator (`financial-analyst/scripts/ratio_calculator.py`)

**Purpose:** Calculate and interpret financial ratios from statement data

**Features:**
- Profitability ratios (ROE, ROA, Gross/Operating/Net Margin)
- Liquidity ratios (Current, Quick, Cash)
- Leverage ratios (Debt-to-Equity, Interest Coverage, DSCR)
- Efficiency ratios (Asset/Inventory/Receivables Turnover, DSO)
- Valuation ratios (P/E, P/B, P/S, EV/EBITDA, PEG)
- Built-in interpretation and benchmarking

**Usage:**
```bash
python financial-analyst/scripts/ratio_calculator.py financial_data.json
python financial-analyst/scripts/ratio_calculator.py financial_data.json --format json
```

### 2. DCF Valuation (`financial-analyst/scripts/dcf_valuation.py`)

**Purpose:** Discounted Cash Flow enterprise and equity valuation

**Features:**
- Revenue and cash flow projections
- WACC calculation (CAPM-based)
- Terminal value (perpetuity growth and exit multiple methods)
- Enterprise and equity value derivation
- Two-way sensitivity analysis
- No external dependencies (uses math/statistics)

**Usage:**
```bash
python financial-analyst/scripts/dcf_valuation.py valuation_data.json
python financial-analyst/scripts/dcf_valuation.py valuation_data.json --format json
```

### 3. Budget Variance Analyzer (`financial-analyst/scripts/budget_variance_analyzer.py`)

**Purpose:** Analyze actual vs budget vs prior year performance

**Features:**
- Variance calculation (actual vs budget, actual vs prior year)
- Materiality threshold filtering
- Favorable/unfavorable classification
- Department and category breakdown

**Usage:**
```bash
python financial-analyst/scripts/budget_variance_analyzer.py budget_data.json
python financial-analyst/scripts/budget_variance_analyzer.py budget_data.json --format json
```

### 4. Forecast Builder (`financial-analyst/scripts/forecast_builder.py`)

**Purpose:** Driver-based revenue forecasting and cash flow projection

**Features:**
- Driver-based revenue forecast model
- 13-week cash flow projection
- Scenario modeling (base/bull/bear)
- Trend analysis from historical data

**Usage:**
```bash
python financial-analyst/scripts/forecast_builder.py forecast_data.json
python financial-analyst/scripts/forecast_builder.py forecast_data.json --format json
```

## Quality Standards

**All finance Python tools must:**
- Use standard library only (math, statistics, json, argparse)
- Support both JSON and human-readable output via `--format` flag
- Provide clear error messages for invalid input
- Return appropriate exit codes
- Process files locally (no API calls)
- Include argparse CLI with `--help` support

## Related Skills

- **C-Level:** Strategic financial decision-making -> `../c-level-advisor/`
- **Business & Growth:** Revenue operations, sales metrics -> `../business-growth/`
- **Product Team:** Budget allocation, RICE scoring -> `../product-team/`

---

**Last Updated:** May 10, 2026
**Skills Deployed:** 3/3 finance skills production-ready
**Total Tools:** 7 Python automation tools
**Commands:** /financial-health, /saas-health

<!-- GSD:project-start source:PROJECT.md -->
## Project

**Finance Skills MCP Server**

A universal MCP (Model Context Protocol) server that wraps the markdown-based finance skills in `skills/` (financial-analyst, saas-metrics-coach, business-investment-advisor) and exposes them to any MCP client as callable tools. The server is built on **FastMCP** with **Claude Code Agents SDK** as the agent runtime. The mechanism is **skill-agnostic** — skills are discovered from disk and indexed at init; no skill names are hardcoded.

The server is the canonical interface to a skill-driven AI agent: a client picks one or more skills from `list_skills`, posts a task, polls for status, and reads the result.

**Core Value:** **Превратить любые markdown-скиллы из каталога `skills/` в программно-вызываемый MCP-сервис без хардкода имён скиллов.** Если завтра в `skills/` появится новый каталог со `SKILL.md` — он автоматически становится частью контракта `list_skills`. Спецификация и потоки должны быть зафиксированы (OpenSpec + PlantUML) **до** написания кода.

### Constraints

- **Tech stack — server**: Python, FastMCP (https://github.com/jlowin/fastmcp) — explicit user choice
- **Tech stack — agent runtime**: Claude Code Agents SDK — explicit user choice
- **Specification framework**: OpenSpec (https://openspec.dev/) — must produce the spec before code
- **Diagram format**: PlantUML for all sequence diagrams (one per MCP tool + one for init)
- **Execution model**: synchronous, single-task — server must block and reject concurrent `create_task` calls
- **Design discipline**: spec + diagrams **before** the implementation phase — non-negotiable gating
- **Universality**: implementation must work on the current 4 skills *and* on any future skill dropped into `skills/` without code change
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3 (system: 3.14.4) - All automation scripts in `skills/financial-analyst/scripts/` and `skills/saas-metrics-coach/scripts/`
- Markdown - All skill definitions, knowledge bases, reference documents, and templates
- JSON - Input/output data format for all Python tools; plugin manifests (`.claude-plugin/plugin.json`)
## Runtime
- Python 3 (stdlib-only, no virtualenv or pip required)
- No Node.js, no Ruby, no compiled language toolchain
- None — all Python scripts use the standard library exclusively
- No `requirements.txt`, no `pyproject.toml`, no `setup.py`, no lockfile
## Frameworks
- None — this is a Claude Code skills repo, not a web application. All execution happens inside the Claude Code agent runtime consuming SKILL.md files.
- Not applicable — no test framework configured
- Not applicable — no build step; scripts run directly with `python3`
## Key Dependencies
- `json` - JSON file parsing and output serialization
- `sys` - stdin/stdout/stderr, exit codes
- `argparse` - CLI argument parsing and `--help` support
- `math` - Mathematical operations (DCF, NPV, IRR)
- `statistics` - `mean()` function for trend analysis
- `typing` - Type hints (`Any`, `Dict`, `List`, `Optional`, `Tuple`)
## Python Tools — Summary
| Script | Location | Purpose |
|--------|----------|---------|
| `ratio_calculator.py` | `skills/financial-analyst/scripts/ratio_calculator.py` | 5-category financial ratio analysis |
| `dcf_valuation.py` | `skills/financial-analyst/scripts/dcf_valuation.py` | DCF enterprise/equity valuation |
| `budget_variance_analyzer.py` | `skills/financial-analyst/scripts/budget_variance_analyzer.py` | Actual vs. budget vs. prior-year variance |
| `forecast_builder.py` | `skills/financial-analyst/scripts/forecast_builder.py` | Driver-based revenue forecast + 13-week cash flow |
| `metrics_calculator.py` | `skills/saas-metrics-coach/scripts/metrics_calculator.py` | Core SaaS metrics (ARR, MRR, churn, CAC, LTV, NRR) |
| `quick_ratio_calculator.py` | `skills/saas-metrics-coach/scripts/quick_ratio_calculator.py` | SaaS Quick Ratio (growth efficiency) |
| `unit_economics_simulator.py` | `skills/saas-metrics-coach/scripts/unit_economics_simulator.py` | 12-month SaaS unit economics projection |
## Configuration
- No environment variables required — all scripts operate on local JSON input files
- No `.env` files present
- Root plugin: `.claude-plugin/plugin.json` — declares `finance-skills` plugin v2.2.3
- Skill plugin: `business-investment-advisor/.claude-plugin/plugin.json` — declares `business-investment-advisor` skill v2.2.2
- Both are MIT-licensed, authored by Alireza Rezvani, hosted at `github.com/alirezarezvani/claude-skills`
- claude-code
- codex-cli
- openclaw
## Platform Requirements
- Python 3.x (any recent version)
- No OS-specific dependencies
- Runs on macOS, Linux, Windows
- Deployed as Claude Code skills — consumed by the Claude Code agent at inference time
- Python tools invoked as subprocess calls from within the agent session
- No server, no database, no container required
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Snake_case for all Python scripts: `ratio_calculator.py`, `dcf_valuation.py`, `budget_variance_analyzer.py`, `forecast_builder.py`, `metrics_calculator.py`, `quick_ratio_calculator.py`, `unit_economics_simulator.py`
- Skill documents use kebab-case: `SKILL.md`, `financial-ratios-guide.md`, `forecasting-best-practices.md`
- PascalCase: `FinancialRatioCalculator`, `DCFModel`, `BudgetVarianceAnalyzer`, `ForecastBuilder`
- Class names describe the thing being built, not the action: `ForecastBuilder` not `ForecastingTool`
- Snake_case for all functions and methods
- Top-level calculation functions: `calculate()`, `simulate()`, `calculate_quick_ratio()`
- Formatting functions always named `format_report()` or `format_text()` — no variation
- Entry point always named `main()`
- Class methods use verb-noun pairs: `calculate_profitability()`, `build_rolling_cash_flow()`, `generate_executive_summary()`
- Snake_case throughout
- Abbreviations accepted only for well-known finance terms: `mrr`, `arr`, `cac`, `ltv`, `nrr`, `arpa`, `wacc`, `fcf`, `eps`
- Descriptive names for intermediate values: `budget_var_amt`, `budget_var_pct`, `pv_fcf`
- SCREAMING_SNAKE_CASE as class-level dicts: `BENCHMARKS` in `FinancialRatioCalculator`
- Kept as class attributes, not module-level globals
- Positional required params use short descriptive names: `numerator`, `denominator`
- Optional params have defaults at definition: `threshold_pct: float = 10.0`, `gross_margin=0.70`
## Code Style
- No formatter config file present (no `.prettierrc`, `pyproject.toml`, or `ruff.toml`)
- Style is PEP 8-compliant by inspection
- 4-space indentation throughout
- Lines kept under ~88 characters; long argument lists broken across lines with trailing comma alignment
- Blank lines: two between top-level definitions, one between class methods
- Financial-analyst scripts (`ratio_calculator.py`, `dcf_valuation.py`, `budget_variance_analyzer.py`, `forecast_builder.py`) fully annotated using `typing` module: `Dict[str, Any]`, `List[float]`, `Optional[str]`, `Tuple[float, float, float]`
- SaaS-metrics scripts (`metrics_calculator.py`, `quick_ratio_calculator.py`, `unit_economics_simulator.py`) use NO type annotations — bare function signatures with positional and keyword args only
- Use `from typing import Any, Dict, List, Optional, Tuple` (pre-3.9 style — do not use built-in generics like `list[float]`)
- No linting config files detected (no `.flake8`, `.pylintrc`, `ruff.toml`)
- No `# noqa` suppression comments anywhere in the codebase
## Import Organization
- None. No `src/` layout, no `__init__.py` files — scripts are standalone.
- Standard library only. No third-party imports anywhere. NEVER introduce `numpy`, `pandas`, `scipy`, or any pip-installable package.
## Module Design
- No `__all__` definitions. Public surface is whatever the file defines.
- Importable functions are designed for use by other scripts: `from metrics_calculator import calculate, report`
- Class-based scripts are not designed for import — only for CLI invocation.
## Shared Utility Function
## Output Format Pattern
## Error Handling
- All error messages print to `sys.stderr`, never `sys.stdout`
- Error messages always start with `"Error: "`
- `sys.exit(1)` on all error paths; `sys.exit(0)` (implicit or explicit) on success
- `ValueError` caught separately inside processing logic for model-level failures (e.g., missing historical data in DCF)
- SaaS-metrics scripts have no file-reading error handling — they receive data via CLI args directly
## Logging
- No logging framework. No `import logging`.
- User-facing output: `print()` to stdout.
- Error messages: `print(..., file=sys.stderr)`.
## Comments
- Every script starts with a triple-quoted module docstring immediately after the shebang
- Docstrings include: one-line description, blank line, paragraph of features, blank line, `Usage:` block with 2-3 example invocations
- Single-line or short multi-line. Financial-analyst scripts include `Args:` sections in `__init__` when parameters need explanation.
- Inline comments used sparingly for non-obvious finance logic: CAPM formula, perpetuity growth method, DSO inversion note
- All scripts start with `#!/usr/bin/env python3`
## Function Design
- Class methods kept to one logical operation (calculate one category, format one section)
- Orchestrator methods (`run_full_valuation`, `run_analysis`, `run_full_forecast`) call sub-methods and aggregate results — they contain no raw computation
- Class-based: pass data in `__init__`, expose results via methods
- Function-based: all inputs as keyword arguments with defaults; no `**kwargs`
- Calculation methods return `Dict[str, Any]` for structured data
- Formatting methods always return `str` (never print directly from within)
- The `main()` function has no return value (`-> None`)
- JSON-serializable dicts only: no custom objects, no `datetime` in output, `float('inf')` sanitized to `None` before JSON serialization
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## System Overview
```text
```
## Component Responsibilities
| Component | Responsibility | File |
|-----------|----------------|------|
| Root plugin manifest | Declares the multi-skill package to agent runtimes | `.claude-plugin/plugin.json` |
| Nested plugin manifest | Declares the standalone business-investment-advisor skill | `business-investment-advisor/.claude-plugin/plugin.json` |
| finance-skills meta-skill | Index / quick-start guide pointing to financial-analyst | `skills/finance-skills/SKILL.md` |
| financial-analyst skill | Ratio analysis, DCF valuation, budget variance, forecasting — 4 Python tools | `skills/financial-analyst/SKILL.md` |
| saas-metrics-coach skill | SaaS health: ARR, MRR, churn, CAC, LTV, NRR, Quick Ratio — 3 Python tools | `skills/saas-metrics-coach/SKILL.md` |
| business-investment-advisor skill | Investment thesis evaluation, ROI/NPV/IRR modeling, capital allocation — no scripts | `business-investment-advisor/skills/business-investment-advisor/SKILL.md` |
| Ratio Calculator | Calculate + interpret 20+ financial ratios across 5 categories | `skills/financial-analyst/scripts/ratio_calculator.py` |
| DCF Valuation | WACC/CAPM, 5-year projections, terminal value, sensitivity analysis | `skills/financial-analyst/scripts/dcf_valuation.py` |
| Budget Variance Analyzer | Actual vs budget vs prior year, materiality filtering, favorable/unfavorable | `skills/financial-analyst/scripts/budget_variance_analyzer.py` |
| Forecast Builder | Driver-based revenue forecast, 13-week cash flow, scenario modeling | `skills/financial-analyst/scripts/forecast_builder.py` |
| Metrics Calculator | Core SaaS metrics: ARR, MRR growth, churn, CAC, LTV, NRR | `skills/saas-metrics-coach/scripts/metrics_calculator.py` |
| Quick Ratio Calculator | Growth efficiency: (New MRR + Expansion) / (Churned + Contraction) | `skills/saas-metrics-coach/scripts/quick_ratio_calculator.py` |
| Unit Economics Simulator | 12-month forward projection of SaaS unit economics | `skills/saas-metrics-coach/scripts/unit_economics_simulator.py` |
| Codex instructions | Codex CLI agent instructions for tool invocation | `.codex/instructions.md` |
| CLAUDE.md | Claude Code project instructions and tool overview | `CLAUDE.md` |
## Pattern Overview
- Skills are documents first: SKILL.md is the primary artifact that the AI agent loads and follows
- Python scripts are optional execution tools invoked by the agent or user via CLI; they do not import each other
- No runtime coupling between skills — they communicate only through the agent's context window
- Plugin manifests (`plugin.json`) expose the package to multiple agent runtimes (Claude Code, Codex, Gemini CLI, Cursor, OpenClaw)
- Two plugin roots exist: top-level (multi-skill bundle) and `business-investment-advisor/` (single-skill distribution)
## Layers
- Purpose: Declares skill packages to agent runtimes
- Location: `.claude-plugin/plugin.json`, `business-investment-advisor/.claude-plugin/plugin.json`
- Contains: Name, description, version, author, `"skills"` directory pointer
- Depends on: Nothing (static JSON)
- Used by: Agent runtime (Claude Code, Codex, etc.) at install/load time
- Purpose: Agent behavior definition — what the AI should do, in what order, with what output format
- Location: `skills/*/SKILL.md`, `business-investment-advisor/skills/*/SKILL.md`
- Contains: YAML frontmatter (name, description, tags), workflow steps, tool invocation instructions, output format specs
- Depends on: References and assets within the same skill directory
- Used by: AI agent at conversation time (loaded into context)
- Purpose: Deterministic calculation and data processing that supplements AI reasoning
- Location: `skills/financial-analyst/scripts/`, `skills/saas-metrics-coach/scripts/`
- Contains: Standalone CLI scripts — each is a complete, independent program
- Depends on: Python standard library only (`math`, `statistics`, `json`, `argparse`, `datetime`, `sys`)
- Used by: AI agent (via shell invocation) or user directly via terminal
- Purpose: Reference material the AI loads on demand to inform analysis
- Location: `skills/financial-analyst/references/`, `skills/saas-metrics-coach/references/`
- Contains: Markdown documents covering formulas, benchmarks, industry adaptations, methodology
- Depends on: Nothing
- Used by: AI agent during analysis phases
- Purpose: Structured output templates and sample data for testing/onboarding
- Location: `skills/financial-analyst/assets/`, `skills/saas-metrics-coach/assets/`
- Contains: Report templates (`.md`), sample JSON input files, expected output JSON
- Depends on: Nothing
- Used by: AI agent when generating reports; developers when testing scripts
## Data Flow
### Primary Flow: Agent-Driven Analysis
### Secondary Flow: Direct CLI Usage
### Flow: Multi-Skill Composition
- No persistent state. Scripts are stateless functions: input JSON in, results to stdout.
- All state lives in the agent's conversation context between turns.
## Key Abstractions
- Purpose: Defines what an AI agent should know and do for a given domain
- Examples: `skills/financial-analyst/SKILL.md`, `skills/saas-metrics-coach/SKILL.md`, `business-investment-advisor/skills/business-investment-advisor/SKILL.md`
- Pattern: YAML frontmatter (machine-readable metadata) + Markdown body (human/agent-readable instructions with workflow steps, tool usage, and output format)
- Purpose: Makes the skill package discoverable and installable by agent runtimes
- Examples: `.claude-plugin/plugin.json`, `business-investment-advisor/.claude-plugin/plugin.json`
- Pattern: JSON with `name`, `description`, `version`, `author`, `skills` (directory path)
- Purpose: Deterministic financial computation with no external dependencies
- Examples: All 7 `.py` files in `skills/*/scripts/`
- Pattern: `argparse` CLI → `json.load()` input → class-based or function-based computation → `json.dumps()` or formatted text to stdout. Every script supports `--format json` or `--json` flag and exits with code 1 on error.
- Purpose: Authoritative domain knowledge the agent consults during analysis
- Examples: `skills/financial-analyst/references/financial-ratios-guide.md`, `skills/saas-metrics-coach/references/benchmarks.md`
- Pattern: Standalone Markdown — no cross-references to other skills, no dynamic content
## Entry Points
- Location: `.claude-plugin/plugin.json`
- Triggers: Agent runtime install or skill discovery
- Responsibilities: Names the package, points to `skills/` directory
- Location: `business-investment-advisor/.claude-plugin/plugin.json`
- Triggers: Agent runtime install of standalone business-investment-advisor
- Responsibilities: Names the single-skill package, points to `skills/` subdirectory
- Location: `skills/financial-analyst/scripts/ratio_calculator.py` (`if __name__ == "__main__": main()`)
- Triggers: `python ratio_calculator.py <json_file> [--format json] [--category <name>]`
- Responsibilities: Parse args, load JSON, run ratio calculations, output results
- Location: `skills/financial-analyst/scripts/dcf_valuation.py`
- Triggers: `python dcf_valuation.py <json_file> [--format json] [--projection-years N]`
- Responsibilities: Parse args, build DCF model, compute WACC and terminal value, output valuation
- Location: `skills/financial-analyst/scripts/budget_variance_analyzer.py`
- Triggers: `python budget_variance_analyzer.py <json_file> [--format json] [--threshold-pct N] [--threshold-amt N]`
- Responsibilities: Parse args, compute variances, apply materiality filter, output report
- Location: `skills/financial-analyst/scripts/forecast_builder.py`
- Triggers: `python forecast_builder.py <json_file> [--format json] [--scenarios base,bull,bear]`
- Responsibilities: Parse args, build driver-based forecast, run scenarios, output projections
- Location: `skills/saas-metrics-coach/scripts/metrics_calculator.py`
- Triggers: Interactive (`python metrics_calculator.py`) or CLI (`python metrics_calculator.py --mrr N --customers N ...`)
- Responsibilities: Calculate core SaaS metrics (ARR, MRR growth, churn, CAC, LTV, NRR), output report or JSON
- Location: `skills/saas-metrics-coach/scripts/quick_ratio_calculator.py`
- Triggers: `python quick_ratio_calculator.py --new-mrr N --expansion N --churned N [--contraction N] [--json]`
- Responsibilities: Calculate Quick Ratio, interpret against benchmarks, output result
- Location: `skills/saas-metrics-coach/scripts/unit_economics_simulator.py`
- Triggers: `python unit_economics_simulator.py --mrr N --growth N --churn N --cac N [--json]`
- Responsibilities: Project SaaS unit economics 12 months forward, output monthly table
## Architectural Constraints
- **No external dependencies:** All Python scripts use only the standard library. `import numpy`, `import pandas`, `import scipy` are forbidden. New scripts must follow this constraint.
- **No shared modules:** Scripts do not import each other. Each script is independently executable with no installation step. There is no `__init__.py`, no package, no `setup.py`.
- **No persistent storage:** Scripts read from an input JSON file and write to stdout. No database, no file writes, no network calls.
- **Dual distribution model:** The repo supports two plugin roots simultaneously. The top-level `.claude-plugin/plugin.json` packages all 3 skills; `business-investment-advisor/.claude-plugin/plugin.json` packages only the business-investment-advisor skill for standalone distribution.
- **business-investment-advisor has no scripts:** This skill is instruction-only — all analysis is performed by the AI agent using the frameworks and formulas defined in SKILL.md. No Python automation tools exist for it.
- **Metrics calculator dual-mode:** `metrics_calculator.py` uniquely supports both interactive mode (prompts user for input) and CLI flag mode (`--mrr`, `--customers`, etc.). Other scripts are CLI-only.
## Anti-Patterns
### Importing Between Scripts
### Adding External Dependencies
### Putting Business Logic in SKILL.md
## Error Handling
- File not found: Print `Error: File '<path>' not found.` to `sys.stderr`, `sys.exit(1)`
- Invalid JSON: Print `Error: Invalid JSON in '<path>': <details>` to `sys.stderr`, `sys.exit(1)`
- Division by zero: Handled via `safe_divide()` helper (returns 0.0 by default) — used in ratio calculations to avoid crashes on missing financial data
- Missing optional fields: Scripts use `.get()` with sensible defaults rather than raising `KeyError`
## Cross-Cutting Concerns
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
