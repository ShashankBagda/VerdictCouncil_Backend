.PHONY: install prefetch-sanitizer lint typecheck test migrate reset-db infra-up infra-down dev clean openapi-snapshot openapi-check smoke-contract

install: ## Install dependencies
	python3.12 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"
	@$(MAKE) prefetch-sanitizer || echo "warn: sanitizer model prefetch failed (no internet?). First upload will be slow."

prefetch-sanitizer: ## Pre-download the llm-guard DeBERTa-v3 classifier model (~415 MB, one-time)
	.venv/bin/python -m scripts.prefetch_sanitizer_model

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

reset-db: ## Wipe and recreate schema from models, then stamp alembic to head
	.venv/bin/python -m scripts.reset_db

infra-up: ## Start local infrastructure (PostgreSQL, Redis)
	docker compose -f docker-compose.infra.yml up -d

infra-down: ## Stop local infrastructure
	docker compose -f docker-compose.infra.yml down

dev: ## Start the API and arq worker via honcho
	.venv/bin/honcho -f Procfile.dev start

clean: ## Remove build artifacts
	rm -rf .venv build dist *.egg-info .pytest_cache .mypy_cache htmlcov .coverage

openapi-snapshot: ## Regenerate docs/openapi.json from the FastAPI app
	.venv/bin/python -m scripts.export_openapi docs/openapi.json

openapi-check: openapi-snapshot ## Fail if docs/openapi.json is out of sync with the app
	@git diff --exit-code docs/openapi.json \
		|| (echo "docs/openapi.json is out of date — run 'make openapi-snapshot' and commit the diff"; exit 1)

smoke-contract: ## Hit every frontend-used endpoint against a running API (needs seed)
	.venv/bin/python -m scripts.smoke_frontend_contract

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
