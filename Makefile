# Everyday commands. `make help` lists them.
.DEFAULT_GOAL := help
SHELL := /bin/bash
ADMIN_URL ?= postgresql+psycopg://ic_owner:ic_owner@localhost:5432/postgres
DB ?= insightfulcell
OWNER_URL := $(subst /postgres,/$(DB),$(ADMIN_URL))
# uv manages backend/.venv itself; an activated venv elsewhere only produces warnings.
unexport VIRTUAL_ENV

.PHONY: help bootstrap up down migrate dev api worker web allow-origin check check-fast fix e2e contract hooks clean

help: ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

bootstrap: ## One-time setup: dependencies, .env files, git hooks
	scripts/bootstrap.sh

up: ## Start PostgreSQL and S3 storage (Docker)
	@command -v docker >/dev/null || { echo "Docker is not installed. See README.md, 'Quick start'."; exit 127; }
	docker compose up -d --wait
	cd backend && uv run python ../scripts/s3_setup.py --endpoint http://localhost:9000 --web-env ../frontend/.env.local

down: ## Stop PostgreSQL and S3 storage (data is kept)
	docker compose down

migrate: ## Create or update the database schema (API tables and Better Auth tables)
	@source scripts/lib.sh && db_reachable $(ADMIN_URL) || { echo "PostgreSQL is not reachable on localhost:5432. Run 'make up' first."; exit 1; }
	cd backend && uv run python ../scripts/db_bootstrap.py create-db --database $(DB) --admin-url $(ADMIN_URL)
	cd backend && MIGRATION_DATABASE_URL=$(OWNER_URL) uv run alembic upgrade head
	cd backend && uv run python ../scripts/db_bootstrap.py create-logins --database $(DB) --admin-url $(ADMIN_URL)
	cd frontend && pnpm auth:migrate

dev: ## Run the API, the worker and the web app together (Ctrl-C stops all three)
	scripts/dev.sh

api: ## Run the API with auto-reload on :8000
	cd backend && uv run uvicorn insightfulcell.api.main:app --reload --port 8000 --timeout-graceful-shutdown 2

worker: ## Run the worker: analyses stay queued until one runs (and upkeep)
	cd backend && uv run python -m insightfulcell.worker

web: ## Run the web app on :3000
	cd frontend && pnpm dev

allow-origin: ## Open the app from another address too: make allow-origin ORIGIN=https://….ngrok-free.app
	scripts/allow-origin.sh $(ORIGIN)

check: ## Everything CI runs. Run before you push
	scripts/check.sh

check-fast: ## Same, without the slow dependency audits
	scripts/check.sh --fast

fix: ## Auto-format and auto-fix lint problems
	scripts/check.sh --fix --fast

e2e: ## Browser test of the full stack (sign in, upload, live analysis)
	scripts/check.sh --e2e --fast

contract: ## Regenerate openapi.json and the typed frontend client
	scripts/generate-contract.sh

hooks: ## Install git hooks (format and secrets on commit, commit-message check)
	scripts/install-hooks.sh

clean: ## Remove build output and caches
	rm -rf .e2e frontend/.next frontend/coverage backend/.coverage backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache
