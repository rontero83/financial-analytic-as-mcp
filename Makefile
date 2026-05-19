# Makefile — local-dev wrappers for Phase 0 toolchain (D-17)
# CI mirrors these commands in .github/workflows/ci.yml (added in PLAN 05).
# If you bump a tool version here, bump it in ci.yml too.

DIAGRAMS_DIR   := docs/diagrams
# Pin PlantUML image tag explicitly (Pitfall 2). The placeholder PLANTUML_TAG below
# is set to :latest until PLAN 03 Task 0 runs `docker pull` and writes back the
# resolved tag (e.g., plantuml/plantuml:1.2026.5). DO NOT leave :latest in any
# subsequent commit — PLAN 03 enforces the pin.
PLANTUML_TAG   := plantuml/plantuml:latest
OPENSPEC_PIN   := @fission-ai/openspec@1.3.1

.PHONY: all spec diagrams test test-contract test-live clean help

help:
	@echo "Targets: spec, diagrams, test, test-contract, test-live, all, clean"

spec:
	npx --yes @fission-ai/openspec@1.3.1 validate --all

diagrams:
	docker run --rm --user "$$(id -u):$$(id -g)" -v "$(PWD):/data" $(PLANTUML_TAG) -tsvg /data/$(DIAGRAMS_DIR)/*.puml

test:
	uv run pytest -m "not live"

test-contract:
	uv run pytest tests/unit -m contract -v

test-live:
	uv run pytest tests/integration_live -m live

all: spec diagrams test

clean:
	rm -rf .venv .pytest_cache .ruff_cache .mypy_cache **/__pycache__ dist build
