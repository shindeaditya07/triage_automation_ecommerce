# ReplyDesk

**Grounded support triage for small e-commerce stores.**

ReplyDesk reads each incoming customer message, looks up the real order and the store's policy, and
drafts a reply in which every factual claim is cited and checked. The store owner approves, edits
or rejects every reply. Nothing is sent without that decision.

It is built around a fictional Indian home goods store, **Kaveri Home Goods**, and uses a mock
order store. The pipeline does not depend on that store: it needs an order API and a policy document.

[![CI](https://github.com/shindeaditya07/triage_automation_ecommerce/actions/workflows/ci.yml/badge.svg)](https://github.com/shindeaditya07/triage_automation_ecommerce/actions/workflows/ci.yml)

## Contents

- [What it does](#what-it-does)
- [Architecture](#architecture)
- [Run it locally](#run-it-locally)
- [Tests and evals](#tests-and-evals)
- [Evaluation results](#evaluation-results)
- [Deployment](#deployment)
- [Documentation](#documentation)
- [Known limitations](#known-limitations)

## What it does

1. **Takes in** a message from the contact form or an HMAC-signed email webhook. Card, UPI and
   Aadhaar patterns are masked before anything is stored or sent to a model, and a repeat of the
   same message is one ticket.
2. **Classifies** it: one of ten intents, an urgency, a confidence and flags.
3. **Escalates early**, in plain code with no model call: abusive tone, a legal threat, suspected
   fraud, a prompt injection, low confidence, or no policy workflow. These go to the owner with no
   draft.
4. **Looks up the order**, but only if the sender owns it. A message quoting someone else's order
   is escalated and no order data is kept.
5. **Escalates again** with the order known: a missing order, a high-value order, a repeat
   complaint. A return or damage report outside its window gets a draft marked *suggestion only*,
   which the owner cannot approve in one click.
6. **Retrieves** the policy passages the intent requires, plus a vector search over the policy.
7. **Drafts** a reply that sees only the order snapshot and those passages, with citations.
8. **Checks the draft** with ten deterministic rules (G-1 to G-10) and then a judge model from a
   different provider. A draft that fails is retried once, then replaced by a standard template
   that makes no claims.
9. **Shows the owner** the message, the draft and the evidence side by side. Edits are re-checked
   as the owner types, and sending over a warning needs a written reason.
10. **Records everything** in an audit log and a metrics dashboard computed from stored data.

## Architecture

```
 Contact form / signed email webhook
            │
            ▼
   ┌──────────────────┐        ┌───────────────────────────────────────┐
   │  Web (React,     │  /api  │  API (FastAPI, Python 3.12)           │
   │  Vite, nginx)    │───────▶│                                       │
   └──────────────────┘        │  intake ─▶ job table ─▶ worker        │
                               │                          │            │
                               │  classify ─▶ escalation ─▶ lookup ────┼──▶ Mock store
                               │  ─▶ retrieval ─▶ draft ─▶ grounding   │    (FastAPI,
                               │  ─▶ approval queue ─▶ mock send       │     31 seeded
                               │                                       │     edge cases)
                               │  ModelGateway ───────────────────────┼──▶ LLM providers
                               └───────────────┬───────────────────────┘
                                               │
                                  ┌────────────▼─────────────┐
                                  │ Postgres 16 + pgvector   │
                                  │ tickets, drafts, jobs,   │
                                  │ audit_log, snapshots,    │
                                  │ policy_embeddings        │
                                  └──────────────────────────┘
```

- **One process, one database.** Background work is a Postgres job table
  (`FOR UPDATE SKIP LOCKED`), not an in-memory queue, so a restart loses nothing. A sweeper
  re-queues stuck jobs. Every pipeline step is safe to run twice.
  See [ADR 0001](docs/adr/0001-concurrency.md) and [ADR 0002](docs/adr/0002-job-runner.md).
- **Models are swappable.** Everything goes through `ModelGateway`, which adds retries, a circuit
  breaker, schema checks and cost records. A model failure escalates the ticket and never
  produces a draft. See [ADR 0003](docs/adr/0003-llm-provider.md).
- **Auth** is an owner login with argon2, short-lived tokens in httpOnly cookies, and lockout.
  See [docs/auth.md](docs/auth.md).

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript (strict), Vite, React Router, TanStack Query, types generated from the OpenAPI schema |
| Backend | FastAPI, Python 3.12, uv, Pydantic v2, SQLAlchemy 2 Core with asyncpg, Alembic |
| Data | PostgreSQL 16 with pgvector |
| Checks | ruff, mypy (strict), pytest, vitest, eslint, prettier, axe-core, detect-secrets, pip-audit |
| Delivery | Docker (multi-stage), Docker Compose, GitHub Actions, Render |

## Run it locally

You need Docker, [uv](https://docs.astral.sh/uv/) and Node 20 or later.

```bash
git clone https://github.com/shindeaditya07/triage_automation_ecommerce.git
cd triage_automation_ecommerce
cp .env.example .env
make owner-hash          # asks for a password, prints OWNER_PASSWORD_HASH
```

Put the hash in `.env` as `OWNER_PASSWORD_HASH`, and set `OWNER_EMAIL`, `SECRET_KEY` and
`HMAC_SECRET`. With the default `LLM_PROVIDER=fake` no model is called and no key is needed; tickets
are received but not drafted. To get real drafts, set `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL`
and the `JUDGE_*` and `EMBED_*` settings described in `.env.example`, then run `make index-policies`.

```bash
make up                  # db, api, mock-store, web
make migrate
```

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API docs | http://localhost:8000/docs |
| Liveness / readiness | http://localhost:8000/healthz, http://localhost:8000/readyz |
| Mock store | http://localhost:8001 |

Log in at the web app with `OWNER_EMAIL` and the password you hashed. Send a test ticket as a
customer would, using an email address and order from the mock store (`services/mock-store/seed/seed.json`
lists them; for example, `asha.rao@example.com` owns `RD-10001`):

```bash
curl -X POST http://localhost:8000/api/v1/tickets/form \
  -H "Content-Type: application/json" \
  -d '{"email": "asha.rao@example.com", "name": "Asha", "subject": "Where is my order?",
       "message": "Hi, can you tell me the status of RD-10001?", "order_id": "RD-10001"}'
```

The response is `202 {"status": "received"}`. The ticket then appears in the queue.

## Tests and evals

```bash
make lint          # ruff, mypy, eslint, prettier
make test          # backend and frontend tests, no network, no live model
make eval-smoke    # eval harness on recorded fixtures (what CI runs)
make eval          # full eval against a live model (costs money; needs keys)
make audit         # pip-audit and npm audit
make policy-check  # policy constants match the locked policies.md
```

The database tests in `tests/db/` need Postgres and are skipped without `TEST_DATABASE_URL`; CI
runs them against a real database. They include
[`test_all_31_cases.py`](tests/db/test_all_31_cases.py), which sends each of the 31 seeded
edge cases through the whole pipeline and checks where it lands.

## Evaluation results

**No live-model eval has been run yet.** Until one is, there is no measured result for any real
model, and the table below is deliberately empty.

| Date | Drafter | Judge | Intent accuracy | Escalation recall | Grounding failures | Cost / ticket |
|---|---|---|---|---|---|---|
| not yet run | | | | | | |

What exists today is the smoke run (2026-10-22, `smoke-622461a4`, policy v1.1). It replays recorded
fixtures with seeded mistakes, so it tests the harness, not a model. Two of its numbers are real
measurements:

| Check | Result |
|---|---|
| Keyword baseline, intent accuracy (78 labeled tickets) | 0.795 (95% CI 0.69 to 0.87) |
| Keyword baseline, escalation recall (28 tickets) | 0.500 (0.33 to 0.67) |
| Grounding validator on 40 seeded bad drafts | 40 caught, right rule reported for all 40 |
| Grounding validator on 24 good drafts | none wrongly blocked |

The baseline is the floor a model has to beat. With 78 labeled tickets one miss moves a rate a
lot, which is why every figure carries a Wilson interval. The labeled set is synthetic and written
by the developer, so results on real traffic may differ. Full runs are saved under
`evals/results/`.

## Deployment

| Environment | Trigger | Where |
|---|---|---|
| Local | `make up` | Docker Compose |
| CI | every pull request | GitHub Actions, no live model |
| Staging | merge to `develop` | Render, via the `Staging Deploy` workflow |
| Production | not set up yet | |

The flow is `feature/*` to `develop` to `release/*` to `main`, by pull request. Staging runs two
Render services (API and mock store), a managed Postgres with pgvector, and a Static Site for the
web app. The Static Site needs a manual redeploy for web changes, and its security headers are
typed into the Render dashboard (the same values are in `apps/web/nginx.conf`). See
[docs/branching-and-environments.md](docs/branching-and-environments.md) and
[docs/security-headers-and-a11y.md](docs/security-headers-and-a11y.md). A production workflow, an
uptime monitor and a release tag are still to do.

For what to do when something breaks, read [docs/runbook.md](docs/runbook.md).

## Documentation

| Document | What is in it |
|---|---|
| [docs/spec.md](docs/spec.md) | Behavior specification, the source of truth |
| [docs/pipeline.md](docs/pipeline.md) | How a ticket moves through the stages |
| [docs/grounding.md](docs/grounding.md) | The ten grounding rules and the judge |
| [docs/policies.md](docs/policies.md) | Store policy with stable IDs, cited in every draft |
| [docs/ticket-api.md](docs/ticket-api.md), [docs/frontend-api.md](docs/frontend-api.md) | API used by the web app |
| [docs/metrics.md](docs/metrics.md) | How each dashboard number is computed |
| [docs/auth.md](docs/auth.md), [docs/security-headers-and-a11y.md](docs/security-headers-and-a11y.md) | Login and browser security |
| [docs/runbook.md](docs/runbook.md) | Operations |
| [docs/adr/](docs/adr/) | Decisions: concurrency, job runner, model provider |
| [docs/build-plan.md](docs/build-plan.md) | The build plan |

## Known limitations

- **Mock store only.** There is no Shopify or WooCommerce connector. The order client is the one
  place a real store would plug in.
- **No real sending.** "Send" records a mock send in the audit log. Email delivery is not built.
- **English only.** A non-English message is not detected by code. It escalates only because the
  model is unsure or finds no workflow, so a confident model could draft a reply to it. The live
  eval should check this.
- **No image analysis.** Whether a photo is attached is recorded; its contents are not read.
- **No product catalog.** Product questions are answered from the FAQ in the policy document; the
  rest escalate.
- **Business hours are approximate.** `time_to_send` counts Monday to Saturday, 10:00 to 18:00 IST
  and ignores public holidays.
- **Cost figures depend on you.** Cost per ticket is zero until the price settings are filled in.
- **Free-tier hosting sleeps.** A sleeping Render service delays the first request.
- **Privacy review not done.** The system stores customer personal data. Check it against India's
  DPDP Act with a qualified person before using it with real customers.
- **Synthetic evaluation data.** See above.
- **No data clean-up yet.** `RETENTION_DAYS` is a setting, but nothing deletes old data.
- **No license file yet.**
