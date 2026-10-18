<div align="center">

# ReplyDesk

### Grounded Support Triage for Small E-commerce Stores

A **human-in-the-loop** support automation platform that classifies incoming customer messages, retrieves real order and policy data, and drafts grounded replies — where every factual claim is cited and traceable. The store owner approves every reply before it is sent.

[![CI](https://github.com/shindeaditya07/triage_automation_ecommerce_website/actions/workflows/ci.yml/badge.svg)](https://github.com/shindeaditya07/triage_automation_ecommerce_website/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![TypeScript](https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white)](https://typescriptlang.org)

</div>

---

## Table of Contents

- [What is ReplyDesk?](#what-is-replydesk)
- [The Problem It Solves](#the-problem-it-solves)
- [How It Works](#how-it-works)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Running Tests](#running-tests)
- [Evaluation Results](#evaluation-results)
- [Environments and Deployment](#environments-and-deployment)
- [Documentation](#documentation)
- [Known Limitations](#known-limitations)
- [Roadmap (v2)](#roadmap-v2)
- [License](#license)

---

## What is ReplyDesk?

ReplyDesk is a **multi-agent support triage system** built for small e-commerce merchants. It is designed for a fictional Indian home goods store — **Kaveri Home Goods** — but the architecture is store-agnostic and can be adapted to any store with an order API and a policy document.

The system takes every incoming customer support message through a structured pipeline:

1. **Classifies** the intent (return, refund, order status, damaged item, etc.) and urgency
2. **Retrieves** the relevant order data and policy passages
3. **Drafts** a reply using only retrieved facts — never model memory
4. **Validates** the draft with a deterministic grounding layer before it ever reaches the owner
5. **Presents** the draft to the owner for approval, with full evidence side-by-side

> The owner approves or edits every reply. Nothing is sent automatically.

---

## The Problem It Solves

Small online stores are often run by one person. That person spends hours every day on repetitive support lookups — checking order status, reading return policy, verifying whether a refund applies. Mistakes in replies (wrong dates, promises beyond policy) damage customer trust.

ReplyDesk solves this by:
- Cutting the time to draft a reply from minutes to seconds
- Ensuring every draft is backed by actual order data and store policy — no hallucinations
- Blocking any draft that promises something the policy does not support
- Escalating sensitive tickets (legal threats, fraud, high-value orders) directly to the owner

---

## How It Works

```
Customer message
  │
  ▼
[Intake] ─── PII masking, sanitization, normalization, deduplication
  │
  ▼
[Classify] ─── LLM (temperature=0, structured output) → intent + urgency + confidence + flags
  │
  ▼
[Pre-lookup Escalation] ─── Deterministic rules: abusive, legal, injection, low confidence
  │ (if escalated → owner queue, no draft)
  ▼
[Context Lookup] ─── Fetch order, shipping status, customer history from store API
  │
  ▼
[Post-lookup Escalation] ─── High-value, order mismatch, not found, repeat complaint
  │ (if escalated → owner queue; if outside-policy → suggestion-only draft)
  ▼
[Policy Retrieval] ─── pgvector semantic search + deterministic passages by intent
  │
  ▼
[Draft] ─── LLM sees only retrieved order snapshot + policy passages
  │          Outputs: reply_text + citations[] + proposed_action
  ▼
[Grounding Check] ─── Deterministic validator (rules G-1 to G-10) → LLM judge
  │ (fail → retry once → fallback template + flag for owner)
  ▼
[Approval Queue] ─── Owner sees: original message | draft | evidence panel
  │
  ▼
[Approve / Edit / Reject] ─── Edit triggers grounding warning; override is audit-logged
  │
  ▼
[Mock Send] + [Audit Log] + [Metrics]
```

---

## Key Features

### 🏷️ Multi-Intent Classification
Ten intent categories — `order_status`, `refund`, `return`, `damaged_item`, `product_question`, `cancellation`, `address_change`, `payment`, `warranty`, `other` — with urgency scoring and confidence thresholds.

### 🔒 Grounding Validator (Zero Hallucination Target)
Ten deterministic rules block any draft that:
- States an amount, date, or tracking number not found in the order (`G-1`)
- States a policy value not matching `policy_constants.yaml` (`G-2`)
- Uses outcome-promise language ("we will refund you") (`G-3`)
- Contains a citation to a policy ID or order field that does not exist (`G-4`)
- Makes any uncited factual claim (`G-5`)
- Leaks another customer's order data (`G-6`)
- Echoes an injected customer instruction (`G-7`)
- Missing or incorrect store sign-off (`G-8`)
- More than one apology phrase (`G-9`)
- Exceeds the character limit (`G-10`)

An LLM judge (different model family from the drafter) runs as a second pass to catch semantic violations the rules miss.

### 🚨 Two-Stage Escalation
- **Pre-lookup:** tone, legal keywords, injection signals, low confidence — straight to the owner with no draft
- **Post-lookup:** high-value orders (>₹10,000), order-sender mismatch, repeat complaints, outside-policy requests

Deterministic keyword/regex backstops run alongside the LLM classifier — escalation recall does not depend solely on model judgment.

### 👤 Human Approval Gate
The approval screen shows the original customer message, the draft, and an evidence panel (cited order fields, cited policy passages, grounding rule results, confidence score) side by side. The owner can approve, edit, or reject. Nothing is sent without an explicit approval event in the audit log.

### 📊 Metrics Dashboard
Live metrics computed from stored data: draft acceptance rate, time-to-draft (p50/p95), time-to-send (business-hours-aware), grounding failures by rule, escalation rate by reason, cost per ticket in INR.

### 🔁 Resilient Job Processing
Background pipeline work is handled by a DB-backed job table (`SELECT ... FOR UPDATE SKIP LOCKED`). Jobs survive API restarts; a startup sweeper re-queues anything that was stuck. Every stage is idempotent.

### 🛡️ Security by Design
- JWT in httpOnly/Secure/SameSite=Strict cookies — never localStorage
- Customer text passed to LLMs only in clearly delimited data blocks
- PII masked (card numbers, UPI IDs, Aadhaar patterns) before storage and before any LLM call
- HMAC-authenticated email ingestion endpoint
- Order lookup scoped to `(order_id, sender_email)` — mismatch returns only a flag

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Cloudflare / Reverse Proxy           │
└────────────────┬──────────────────────────┬────────────┘
                 │                          │
         / (frontend)               /api (backend)
                 │                          │
   ┌─────────────▼──────────┐   ┌──────────▼──────────────┐
   │   React + TypeScript   │   │     FastAPI (Python)     │
   │   TanStack Query       │   │                          │
   │   Tailwind CSS         │   │  ┌────────────────────┐  │
   │   OpenAPI-generated    │   │  │  Pipeline Stages   │  │
   │   types                │   │  │  Intake / Classify │  │
   └────────────────────────┘   │  │  Escalation / RAG  │  │
                                │  │  Draft / Ground    │  │
   ┌────────────────────────┐   │  └────────────────────┘  │
   │   Mock Store (FastAPI) │   │                          │
   │   31 seeded edge cases │◄──│  ┌────────────────────┐  │
   └────────────────────────┘   │  │   LLMClient        │  │
                                │  │   (swappable)      │  │
   ┌────────────────────────┐   │  └────────────────────┘  │
   │   Postgres + pgvector  │◄──│                          │
   │   tickets, drafts,     │   │  ┌────────────────────┐  │
   │   audit_log, jobs,     │   │  │  Job Worker        │  │
   │   policy_embeddings,   │   │  │  (DB-backed)       │  │
   │   order_snapshots      │   │  └────────────────────┘  │
   └────────────────────────┘   └──────────────────────────┘
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Frontend** | React 18, TypeScript (strict), TanStack Query, Tailwind CSS |
| **Backend** | FastAPI, Python 3.12, `uv` for package management |
| **Database** | PostgreSQL 16 + pgvector extension |
| **ORM / Migrations** | SQLAlchemy 2.0, Alembic |
| **LLM Interface** | Swappable `LLMClient` adapter (OpenAI / Anthropic / Google) |
| **Validation** | Pydantic v2 (same models generate OpenAPI schema and LLM structured-output schema) |
| **Auth** | Argon2 password hashing, JWT in httpOnly cookies, CSRF double-submit |
| **Type safety** | `openapi-typescript` generates TS types from FastAPI schema |
| **Testing** | pytest, pytest-cov, vitest |
| **Linting** | ruff, mypy (strict), eslint, prettier |
| **Security scanning** | detect-secrets, pip-audit, npm audit |
| **Containerisation** | Docker (multi-stage, non-root), Docker Compose |
| **CI/CD** | GitHub Actions |
| **Deployment** | Render (managed Postgres + pgvector, Docker services) |

---

## Project Structure

```
second_e2e_app/
├── apps/
│   └── web/                    # React + TypeScript frontend
│       └── src/
│           ├── components/     # Ticket queue, approval screen, dashboard
│           ├── pages/          # Route-level pages
│           ├── api/            # Generated types (openapi-typescript)
│           └── hooks/          # TanStack Query hooks
├── services/
│   ├── api/                    # FastAPI backend
│   │   ├── alembic/            # Database migrations
│   │   ├── prompts/            # Versioned, hashed LLM prompt files
│   │   ├── grounding/          # Deterministic validator (G-1–G-10) + LLM judge
│   │   ├── escalation/         # Two-stage escalation rules engine
│   │   ├── pipeline/           # Intake, classify, context, retrieve, draft stages
│   │   ├── llm/                # LLMClient interface + provider adapters + FakeClient
│   │   ├── models/             # Pydantic models (ticket, draft, config)
│   │   └── jobs/               # DB-backed job table worker + sweeper
│   └── mock-store/             # FastAPI mock store with 31 seeded edge cases
├── evals/
│   ├── datasets/               # Labeled tickets (JSONL), bad-draft corpus
│   ├── urgency-rubric.md       # Human rubric for urgency labeling
│   ├── runner.py               # Eval runner (no live LLM in CI)
│   └── results/                # eval run outputs — YYYY-MM-DD-<run-id>/
├── docs/
│   ├── spec.md                 # Behavioral specification (source of truth)
│   ├── policies.md             # Store policy document with stable IDs
│   ├── AGENTS.md               # Engineering rules for coding agents
│   ├── build-plan.md           # 5-phase build plan
│   ├── branching-and-environments.md
│   ├── data-contract.md        # Order/item/refund API schema
│   ├── runbook.md              # Operations guide
│   └── adr/                   # Architecture Decision Records
├── policy_constants.yaml       # Single source for all numeric policy constants
├── docker-compose.yml          # Local dev stack
├── docker-compose.staging.yml
├── docker-compose.prod.yml
├── Makefile                    # Single entry point for all dev tasks
└── .env.example                # Complete, commented environment variable list
```

---

## Getting Started

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) — for the database and services
- [uv](https://docs.astral.sh/uv/) — Python package manager (`pip install uv`)
- [Node.js 20+](https://nodejs.org/) — for the frontend
- [Git](https://git-scm.com/)

### 1. Clone the repository

```bash
git clone https://github.com/shindeaditya07/triage_automation_ecommerce_website.git
cd triage_automation_ecommerce_website
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

```bash
# Required
LLM_PROVIDER=openai            # or anthropic, google
LLM_API_KEY=sk-...             # your LLM provider API key
LLM_MODEL=gpt-4o
JUDGE_MODEL=claude-3-5-sonnet-20241022   # different family from drafter

OWNER_EMAIL=owner@example.com
OWNER_PASSWORD_HASH=           # generate with: python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('yourpassword'))"
HMAC_SECRET=                   # any random string for local dev
```

Everything else has sensible defaults in `.env.example`.

### 3. Start the stack

```bash
make up        # starts: postgres+pgvector, api, mock-store, web
make migrate   # runs Alembic migrations (separate from startup)
make seed      # seeds mock store with 31 edge-case orders
```

### 4. Open the app

| Service | URL |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Mock Store | http://localhost:8001 |
| Liveness | http://localhost:8000/healthz |
| Readiness | http://localhost:8000/readyz |

Log in with the `OWNER_EMAIL` and password you set (before hashing).

### 5. Submit a test ticket

```bash
curl -X POST http://localhost:8000/api/v1/tickets/form \
  -H "Content-Type: application/json" \
  -d '{
    "external_message_id": "test-001",
    "customer_email": "customer@example.com",
    "customer_name": "Priya S.",
    "subject": "Where is my order?",
    "body": "Hi, my order ORD-001 was supposed to arrive 3 days ago but I still havent received it. Can you check?",
    "channel": "form"
  }'
```

Refresh the queue in the browser — the ticket should appear within seconds with a draft ready for review.

---

## Running Tests

```bash
make lint          # ruff + mypy (backend), eslint + prettier (frontend)
make test          # unit + pipeline tests — no live LLM calls, no network
make eval-smoke    # smoke eval using recorded fixtures — checks classification
                   # precision/recall, escalation recall, grounding failure rate
make resilience    # resilience test: DB stop/start, API kill mid-processing
make eval          # full eval with live LLM (requires API key in .env)
make audit         # pip-audit + npm audit for dependency vulnerabilities
make policy-check  # assert policy_constants.yaml matches policies.md
```

### Test coverage expectations

| Module | Target coverage |
|---|---|
| `services/api/grounding/` | ≥ 95% branch coverage (CI-enforced) |
| `services/api/escalation/` | ≥ 95% branch coverage (CI-enforced) |
| `services/mock-store/` | All 31 edge cases covered |

---

## Evaluation Results

> ⚠️ **Results will be populated here after the first full eval run.**
> Run `make eval` and commit the output from `evals/results/` to update this table.

| Run date | Model (drafter) | Judge model | Escalation recall | Order status P/R | Refund P/R | Grounding failure rate | Draft acceptance rate | Cost / ticket |
|---|---|---|---|---|---|---|---|---|
| — | — | — | — | — | — | — | — | — |

All eval results with full metric breakdowns are stored in `evals/results/`.

> Wilson confidence intervals are shown in the full report. With 60–100 labeled tickets, a single miss moves precision and recall significantly — the full report says so explicitly.

---

## Environments and Deployment

| Environment | Purpose | Trigger |
|---|---|---|
| **Local (dev)** | Development | `make up` |
| **CI** | PR validation (no live LLM) | Every push |
| **Staging (UAT)** | Integration testing | Merge to `develop` |
| **Non-prod** | Release candidate validation | Push to `release/*` |
| **Production** | Live demo | `v*` tag push |

For detailed setup of each environment (including Render deployment steps), see [`docs/branching-and-environments.md`](docs/branching-and-environments.md).

### Quick deploy to staging

```bash
git checkout develop
git push origin develop   # → GitHub Actions auto-deploys to staging
```

### Release to production

```bash
git checkout main
git merge release/v1.0.0
git tag v1.0.0
git push origin main --tags   # → GitHub Actions auto-deploys to production
```

---

## Documentation

| Document | Description |
|---|---|
| [`docs/spec.md`](docs/spec.md) | Behavioral specification — source of truth for the project |
| [`docs/policies.md`](docs/policies.md) | Store policy document with stable policy IDs (cited in every draft) |
| [`docs/AGENTS.md`](docs/AGENTS.md) | Engineering rules for coding agents and contributors |
| [`docs/build-plan.md`](docs/build-plan.md) | 5-phase build plan with Makefile targets and CI/CD detail |
| [`docs/branching-and-environments.md`](docs/branching-and-environments.md) | Feature branch map and full environment setup guide |
| [`docs/data-contract.md`](docs/data-contract.md) | Order, item, refund, damage, and courier data schemas |
| [`docs/runbook.md`](docs/runbook.md) | Operations: what can go down, how to detect it, how to recover |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records (concurrency, job runner, LLM provider) |
| [`policy_constants.yaml`](policy_constants.yaml) | All numeric policy constants keyed by policy ID |
| [`evals/urgency-rubric.md`](evals/urgency-rubric.md) | Human rubric for consistent urgency labeling |

---

## Known Limitations

- **No real store integration.** v1 uses a mock store only. The adapter interface is designed so that a Shopify or WooCommerce connector can be swapped in without changing the pipeline.
- **No automatic sending.** Every reply requires explicit owner approval. There is no code path from draft to send that bypasses it.
- **English only.** Non-English messages are escalated to the owner. Multi-language support is a v2 topic.
- **Image analysis not implemented.** Attachment metadata (filename, type, size) is captured and `has_photo` is set, but image content is not analyzed. DMG-3.1 photo validation relies only on whether an image attachment is present.
- **Product catalog not available.** `product_question` tickets are answered from the FAQ only. Queries with no FAQ answer are escalated (spec PRD-6.1). A product catalog is a v2 addition.
- **DPDP Act compliance not verified.** The system handles customer PII. Compliance with India's Digital Personal Data Protection Act should be verified by a qualified professional before real-customer deployment.
- **COD capped at ₹5,000.** Consistent with Kaveri Home Goods policy (PAY-7.1). Orders above this limit via COD cannot exist in the seed data.
- **Evaluation dataset is synthetic.** The labeled eval set is synthetic and written by the developer. Results on real production traffic may differ — the README will be updated when real data is available.

---

## Roadmap (v2)

- [ ] Real Shopify / WooCommerce store adapter
- [ ] Agentic tool-calling loops (multi-step reasoning for complex tickets)
- [ ] Product catalog integration for `product_question` intents
- [ ] Multi-language support (auto-detect and route to a translated pipeline)
- [ ] Image analysis for damage reports (vision model on `damaged_item` tickets)
- [ ] Weekly performance report email to the owner
- [ ] Bulk ticket import from existing helpdesk CSV exports
- [ ] DPDP Act compliance audit and erasure workflow improvements

---

## License

This project is licensed under the [MIT License](LICENSE).

---

<div align="center">

Built by [Aditya Shinde](https://github.com/shindeaditya07)

</div>
