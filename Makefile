.DEFAULT_GOAL := help
COMPOSE ?= docker compose
UV ?= uv

.PHONY: help install up down migrate seed lint test eval eval-smoke resilience build \
	deploy-staging deploy-prod audit policy-check

help: ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	$(UV) sync
	cd apps/web && npm ci

.env:
	cp .env.example .env

up: .env ## Start the full local stack
	$(COMPOSE) up -d --build

down: ## Stop and remove containers
	$(COMPOSE) down

migrate: ## Run Alembic migrations (uses DATABASE_URL from the environment or .env)
	$(UV) run alembic upgrade head

seed: ## Build the 31 mock-store edge cases, check them against the contract, write seed.json
	$(UV) run python -m services.mock-store.seed

lint: ## ruff + mypy (backend), eslint + prettier (frontend)
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy
	cd apps/web && npm run lint && npm run format:check

test: ## Unit and pipeline tests (no live LLM or network)
	$(UV) run pytest
	cd apps/web && npm test

eval: ## Full eval run against the labeled dataset (not yet implemented)
	@echo "eval: not yet implemented (feature/p1-eval-harness)"

eval-smoke: ## Smoke eval against recorded fixtures (not yet implemented)
	@echo "eval-smoke: not yet implemented (feature/p1-eval-harness)"

resilience: ## DB stop/start and API kill tests (not yet implemented)
	@echo "resilience: not yet implemented (Phase 5)"

build: ## Build all Docker images
	$(COMPOSE) build

deploy-staging: ## Deploy staging (done by .github/workflows/staging.yml)
	@echo "deploy-staging: not yet implemented (staging pipeline branch)"

deploy-prod: ## Deploy production (done by .github/workflows/release.yml on a v* tag)
	@echo "deploy-prod: not yet implemented (feature/p5-ci-cd-finalize)"

audit: ## pip-audit + npm audit
	$(UV) run pip-audit
	cd apps/web && npm audit

policy-check: ## Verify policy_constants.yaml and the policies.md SHA-256 lock
	$(UV) run python -m scripts.check_policy_constants
	$(UV) run python -m scripts.check_policy_sha
