.DEFAULT_GOAL := help

PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip
PYTHON_DISCOVERY ?= $(shell command -v python3 2>/dev/null || command -v python3.12 2>/dev/null || command -v python3.11 2>/dev/null || command -v python 2>/dev/null)
PYTHON_BOOTSTRAP ?= $(shell $(PYTHON_DISCOVERY) scripts/select_bootstrap_python.py 2>/dev/null)
NPM ?= npm

.PHONY: help bootstrap generated lock lint typecheck test evidence audit web verify

help:
	@echo "FinReplay contributor commands"
	@echo "  make bootstrap   Create the Python environment and install locked dependencies"
	@echo "  make generated   Check every generated catalog, download, and explorer artifact"
	@echo "  make lock        Verify the pinned Python development environment"
	@echo "  make test        Run the branch-aware Python coverage gate"
	@echo "  make evidence    Verify scenario, review-schema, and tracked-secret evidence"
	@echo "  make audit       Audit the resolved Python dependency environment"
	@echo "  make web         Lint, build, test, and audit the public evidence site"
	@echo "  make verify      Run the complete contributor gate"

bootstrap:
	@if [ -z "$(PYTHON_BOOTSTRAP)" ]; then \
		echo "FinReplay bootstrap requires Python 3.11 or 3.12 on PATH; set PYTHON_BOOTSTRAP=/absolute/path/to/python if needed." >&2; \
		exit 2; \
	fi
	$(PYTHON_BOOTSTRAP) scripts/select_bootstrap_python.py --assert-current
	$(PYTHON_BOOTSTRAP) -m venv .venv
	$(PIP) install --upgrade 'pip==26.2.1'
	$(PIP) install -r requirements-dev.lock
	$(PIP) install -e . --no-deps
	$(NPM) ci --prefix web

generated:
	$(PYTHON) scripts/build_user_catalogs.py --check
	$(PYTHON) scripts/build_public_replaypack_downloads.py --check
	$(PYTHON) scripts/build_public_claim_registry.py --check

lock:
	$(PYTHON) scripts/verify_python_dependency_lock.py

lint:
	.venv/bin/ruff check .

typecheck:
	.venv/bin/mypy src tests scripts

test:
	$(PYTHON) -m pytest --cov=finreplay --cov-report=term-missing

evidence: lock
	$(PYTHON) scripts/verify_scenario_catalog.py
	$(PYTHON) scripts/validate_independent_review_records.py
	$(PYTHON) scripts/verify_public_site_readiness.py
	$(PYTHON) scripts/verify_public_site_deployment.py
	$(PYTHON) scripts/verify_public_github_release.py
	$(PYTHON) scripts/scan_tracked_secrets.py

audit:
	$(PYTHON) -m pip_audit --local

web:
	$(NPM) run lint --prefix web
	$(NPM) test --prefix web
	$(NPM) audit --prefix web

verify: generated lint typecheck test evidence audit web
