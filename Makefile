.DEFAULT_GOAL := help

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
PYTHON_BOOTSTRAP ?= python3
NPM ?= npm

.PHONY: help bootstrap generated lint typecheck test evidence audit web verify

help:
	@echo "FinReplay contributor commands"
	@echo "  make bootstrap   Create the Python environment and install locked web packages"
	@echo "  make generated   Check every generated catalog, download, and explorer artifact"
	@echo "  make test        Run the branch-aware Python coverage gate"
	@echo "  make evidence    Verify scenario, review-schema, and tracked-secret evidence"
	@echo "  make audit       Audit the resolved Python dependency environment"
	@echo "  make web         Lint, build, test, and audit the public evidence site"
	@echo "  make verify      Run the complete contributor gate"

bootstrap:
	$(PYTHON_BOOTSTRAP) -m venv .venv
	$(PIP) install --upgrade 'pip>=26.1.2'
	$(PIP) install -e '.[dev]'
	$(NPM) ci --prefix web

generated:
	$(PYTHON) scripts/build_user_catalogs.py --check
	$(PYTHON) scripts/build_public_replaypack_downloads.py --check
	$(PYTHON) scripts/build_public_claim_registry.py --check

lint:
	.venv/bin/ruff check .

typecheck:
	.venv/bin/mypy src tests scripts

test:
	$(PYTHON) -m pytest --cov=finreplay --cov-report=term-missing

evidence:
	$(PYTHON) scripts/verify_scenario_catalog.py
	$(PYTHON) scripts/validate_independent_review_records.py
	$(PYTHON) scripts/scan_tracked_secrets.py

audit:
	$(PYTHON) -m pip_audit --local

web:
	$(NPM) run lint --prefix web
	$(NPM) test --prefix web
	$(NPM) audit --prefix web

verify: generated lint typecheck test evidence audit web
