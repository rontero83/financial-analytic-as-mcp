# Makefile — local-dev wrappers for Phase 0 toolchain (D-17)
# CI mirrors these commands in .github/workflows/ci.yml.
# If you bump a tool version here, bump it in ci.yml too.
#
# 2026-05-19 specs-unify: dropped OpenSpec CLI in favor of `make spec` running
# the contract tests (format + schema/example validation). Specs and diagrams
# live together under `specs/<capability>/`.

SPECS_DIR      := specs
# PlantUML tag PINNED per Pitfall 2 — :latest is non-deterministic across versions.
# If you bump this, re-render all SVGs (make diagrams) and commit, or CI diagrams-fresh fails.
# Resolved 2026-05-19: plantuml/plantuml:1.2026.3 (digest sha256:75321ef9b2b843196aa497b4cb2b5d78fec47f29d77af7b27f04b8d31d3060ae)
PLANTUML_TAG   := plantuml/plantuml:1.2026.3

.PHONY: all spec diagrams test test-contract test-live clean help

help:
	@echo "Targets: spec, diagrams, test, test-contract, test-live, all, clean"

spec:
	# Was: `npx --yes @fission-ai/openspec@1.3.1 validate --all` until 2026-05-19.
	# Replaced by the SPEC-09 contract test which now also enforces format
	# (Requirement / Scenario headings, 4-hash Scenarios, required H2s).
	uv run pytest tests/unit -m contract -v

diagrams:
	# _JAVA_OPTIONS=-Duser.home=/tmp prevents the JVM from resolving user.home to
	# "?" when the --user UID has no /etc/passwd entry inside the container, which
	# would otherwise create a `?/.java/fonts/` cache dir at $PWD (Pitfall 6
	# sibling — keeps `git diff --exit-code` clean in CI).
	# Glob `specs/*/*.puml` picks up every diagram next to its spec
	# (specs/_common.puml is a shared header, never !-rendered standalone).
	docker run --rm --user "$$(id -u):$$(id -g)" -e _JAVA_OPTIONS="-Duser.home=/tmp" -v "$(PWD):/data" $(PLANTUML_TAG) -tsvg /data/$(SPECS_DIR)/*/*.puml

test:
	uv run pytest -m "not live"

test-contract:
	uv run pytest tests/unit -m contract -v

test-live:
	uv run pytest tests/integration_live -m live

all: spec diagrams test

clean:
	rm -rf .venv .pytest_cache .ruff_cache .mypy_cache **/__pycache__ dist build
