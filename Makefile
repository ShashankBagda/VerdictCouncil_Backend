.PHONY: install lint typecheck test migrate infra-up infra-down dev clean

install: ## Install dependencies
	python3.12 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"

lint: ## Run linter
	.venv/bin/ruff check src/ tests/
	.venv/bin/ruff format --check src/ tests/

format: ## Auto-format code
	.venv/bin/ruff check --fix src/ tests/
	.venv/bin/ruff format src/ tests/

typecheck: ## Run type checker
	.venv/bin/mypy src/

test: ## Run tests
	.venv/bin/pytest tests/ -v --tb=short

test-cov: ## Run tests with coverage
	.venv/bin/pytest tests/ -v --cov=src --cov-report=term-missing

migrate: ## Run database migrations
	.venv/bin/python -m alembic upgrade head

infra-up: ## Start local infrastructure (PostgreSQL, Redis, Solace)
	docker compose -f docker-compose.infra.yml up -d

infra-down: ## Stop local infrastructure
	docker compose -f docker-compose.infra.yml down

dev: ## Start all agents via honcho (hybrid mode)
	.venv/bin/honcho -f Procfile.dev start

clean: ## Remove build artifacts
	rm -rf .venv build dist *.egg-info .pytest_cache .mypy_cache htmlcov .coverage

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
