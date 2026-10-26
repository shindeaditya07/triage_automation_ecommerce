.DEFAULT_GOAL := help
COMPOSE ?= docker compose
UV ?= uv

.PHONY: help install up down migrate seed owner-hash lint test eval eval-smoke resilience build \
	deploy-staging deploy-prod audit policy-check index-policies generate-types check-types

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

owner-hash: ## Prompt for the owner password and print its argon2 hash (for OWNER_PASSWORD_HASH)
	@$(UV) run python -m scripts.hash_password

index-policies: ## Embed docs/policies.md into the policy index (needs DATABASE_URL and embedding keys)
	$(UV) run python -m scripts.index_policies

generate-types: ## Regenerate the frontend's API types from the FastAPI OpenAPI schema
	$(UV) run python -m scripts.dump_openapi apps/web/openapi.json
	cd apps/web && npx openapi-typescript openapi.json -o src/api/schema.d.ts
	rm apps/web/openapi.json

check-types: generate-types ## Fail if the committed API types are out of date
	git diff --exit-code -- apps/web/src/api/schema.d.ts

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

eval: ## Live eval: make eval MAX_COST_INR=100 [CPM=10] [LIMIT=5]. Costs money; stops at the limit
	$(UV) run python -m evals.runner --mode full --save $(if $(MAX_COST_INR),--max-cost-inr $(MAX_COST_INR)) $(if $(CPM),--calls-per-minute $(CPM)) $(if $(LIMIT),--limit $(LIMIT))

eval-smoke: ## Smoke eval against recorded fixtures (CI-safe, no model calls)
	$(UV) run python -m evals.runner --mode smoke

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
