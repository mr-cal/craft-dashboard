PROJECT := craft_dashboard
SOURCES := $(PROJECT) tests scripts

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: setup
setup:  ## Install all dependencies
	uv sync --group dev --group lint --group types

.PHONY: format
format:  ## Auto-format code with ruff
	uv run ruff check --fix $(SOURCES)
	uv run ruff format $(SOURCES)

.PHONY: lint
lint:  ## Lint with ruff and check types with ty
	uv run ruff check $(SOURCES)
	uv run ruff format --diff $(SOURCES)
	uv run ty check $(SOURCES)

.PHONY: test
test:  ## Run all tests
	uv run pytest

.PHONY: test-cov
test-cov:  ## Run tests with coverage report
	uv run pytest --cov=$(PROJECT) --cov-report=html --cov-report=term-missing

.PHONY: dev
dev:  ## Run development server with hot reload
	uv run uvicorn craft_dashboard.app:create_app --factory --reload --host 0.0.0.0 --port 8000

.PHONY: migrate
migrate:  ## Apply database migrations
	uv run alembic upgrade head

.PHONY: collect
collect:  ## Run data collection (all sources)
	uv run scripts/collect_data.py --source all

.PHONY: llm
llm:  ## Run LLM evaluation (open issues only)
	uv run scripts/run_llm.py evaluate --open-only

.PHONY: migrate-csv
migrate-csv:  ## One-time CSV migration from starcraft-stats
	uv run scripts/migrate_csv.py

CONTAINER_ENGINE := $(shell command -v podman 2>/dev/null || command -v docker)

.PHONY: build
build:  ## Build the OCI image
	$(CONTAINER_ENGINE) build --build-arg SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0 -t craft-dashboard:latest .

clean:  ## Clean build artifacts and caches
	rm -rf dist build .coverage htmlcov .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
	find . -name "*.pyc" -delete

.PHONY: test-e2e
test-e2e:  ## Run end-to-end tests (requires Docker)
	CRAFT_DASHBOARD_E2E=1 uv run pytest tests/end_to_end/ -v -x --timeout=300
