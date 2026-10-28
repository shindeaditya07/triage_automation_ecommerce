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
- [License](#license)

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
8. **Checks the draft** with ten deterministic rules (G-1 to G-10) and then a judge model (the
   same provider in the measured run; see the evaluation results). A draft that fails is retried
   once, then replaced by a standard template that makes no claims.
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
  produces a draft. See [ADR 0003](docs/adr/0003-llm-provider.md); its last amendment records what
  actually runs.
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
make eval MAX_COST_INR=100 CPM=10   # live eval (costs money; see docs/evals.md)
make audit         # pip-audit and npm audit
make policy-check  # policy constants match the locked policies.md
```

The database tests in `tests/db/` need Postgres and are skipped without `TEST_DATABASE_URL`; CI
runs them against a real database. They include
[`test_all_31_cases.py`](tests/db/test_all_31_cases.py), which sends each of the 31 seeded
edge cases through the whole pipeline and checks where it lands.

## Evaluation results

**Three live runs so far. Two fail the project's own gate on escalation recall (0.95); the third
passes it by one ticket, which is not enough to call the gate met.** All used
`gemini-3.5-flash-lite` as classifier, drafter and judge over the 78 labeled tickets. The first ran
on 2026-10-23; the second on 2026-10-25 after two fixes (non-English messages are escalated by
script, and the code adds the store's sign-off); the third on 2026-10-26 with no code change in
between. Reports for the first two:
[`2026-10-23-full-5a22ad5c`](evals/results/2026-10-23-full-5a22ad5c/report.md) and
[`2026-10-25-full-313ca6f6`](evals/results/2026-10-25-full-313ca6f6/report.md). Run 3
(`full-66397b13`) was not saved: only its printed summary survives, so its row below comes from
that summary and has no report to link.

| Run | Intent accuracy | Escalation recall | Escalation precision | Model drafts passing grounding, first try / either try | Fell back to template | Cost / ticket |
|---|---|---|---|---|---|---|
| 2026-10-23, before the fixes | 0.897 (0.81-0.95) | **0.893 (0.73-0.96)** | 0.926 (0.77-0.98) | 0.552 / 0.810 (n=58) | 11 of 58 | Rs 0.23 |
| 2026-10-25, after the fixes | 0.897 (0.81-0.95) | **0.893 (0.73-0.96)** | 0.926 (0.77-0.98) | 0.897 / 0.966 (n=58) | 2 of 58 | Rs 0.20 |
| 2026-10-26, same code, printed summary only | 0.910 | **0.964 (0.82-0.99)** | not recorded | 0.821 / 0.946 (n=56) | 3 of 56 | Rs 0.21 |

The keyword baseline scored intent 0.795, recall 0.500 and precision 0.737 on the same tickets, so
the model is better on every measure. These three runs used the labels as they were then: on
2026-10-27 T067 (sparks and a burning smell) was relabeled from a draft to a `safety` escalation
when the safety rule was added, so the labeled set now has 29 escalation tickets and the baseline
scores recall 0.517 and precision 0.789. What the three runs show:

- **Drafting improved a lot.** Once the code added the sign-off, first-try grounding went from 55%
  to 90% and fallbacks from 19% to 3%. The 2 remaining fallbacks failed rule G-3 (a promise of an
  outcome) and G-5 (an unsupported claim) twice.
- **The fix for non-English worked.** The Hindi and Marathi tickets are no longer missed.
- **Recall did not improve, and the same number hides different misses.** T029 and T068 (the
  non-English tickets) were fixed, but T042 (a wholesale enquiry) and T044 (a vague "it is not
  working") were escalated in the first run and drafted in the second, with identical prompts.
  The model's answers vary between runs even at temperature 0, so one run is not a stable
  measurement. T052 (a product question the policy cannot answer, which should be suggestion only)
  was missed both times.
- **The 0.95 gate is stricter than 28 escalation cases can support.** One ticket moves recall by
  about 3.5 points. Run 3 got 27 of 28 (only T052 missed; T050 and T067 were falsely escalated)
  with the same code as run 2, so the move from 0.893 to 0.964 is two tickets flipping, not
  an improvement. Pooled over the three runs it is 0.917 (77 of 84), and every run's interval
  includes 0.95.
- The first version of the first report counted the fallback template as a model draft. The
  evaluator is fixed and that report is corrected; see `CORRECTION.md` in its folder.

Limits of these runs: the judge is the same model as the drafter, so grounding is checked less
independently than it appears; the policy embeddings were the fake hashed ones, so vector search
was not measured in these runs (see the next section); the data is synthetic and written by the developer; and each run is a single
sample.

### Retrieval with real embeddings

Measured on 2026-10-28 with `make eval-retrieval` (`gemini-embedding-001`; the same 55 drafted
tickets as the runs above, and the cited passages are the labeled `policy_ids`, tone passages left
out). It compares the fake hashed vectors the smoke eval uses with the real model:

| Measure | Fake vectors | `gemini-embedding-001` |
|---|---|---|
| Cited passages retrieved | 93.3% (97 of 104) | **98.1% (102 of 104)** |
| Tickets with every cited passage | 87.3% (48 of 55) | 96.4% (53 of 55) |
| Cited passages found by the intent rules alone | 80.8% (84 of 104) | 80.8% (84 of 104) |

The intent rules alone find 81%; the vector search supplies the rest, and the real model finds five
more than the fake one. The two tickets still missing a passage are both messages about two topics:
T007 (a refund for a cancelled order) does not get CAN-5.1, and T030 (order status plus a possible
return) does not get RET-1.1. In both, the missing passage belongs to the secondary topic.

Limits: 104 passages is a small sample (two misses move the rate about two points, and the 95%
interval is roughly 93% to 99.5%); the draft is handed about 9 passages on average, so part of the
recall is a wide net, and whether the extra passages distract the drafter is not measured; the
labels and the policy were written by the same person; and it is one run.

What exists today is the smoke run (2026-10-22, `smoke-622461a4`, policy v1.1). It replays recorded
fixtures with seeded mistakes, so it tests the harness, not a model. Two of its numbers are real
measurements:

| Check | Result |
|---|---|
| Keyword baseline, intent accuracy (78 labeled tickets) | 0.795 (95% CI 0.69 to 0.87) |
| Keyword baseline, escalation recall (29 tickets) | 0.517 (0.34 to 0.69) |
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
| Release | tag `v*` on `main` | Builds version-tagged images and publishes a GitHub Release. Nothing is deployed: staging is the only running environment. First release: [v1.0.0](https://github.com/shindeaditya07/triage_automation_ecommerce/releases/tag/v1.0.0). |

The flow is `feature/*` to `develop` to `release/*` to `main`, by pull request. Staging runs two
Render services (API and mock store), a managed Postgres with pgvector, and a Static Site for the
web app. The Static Site needs a manual redeploy for web changes, and its security headers are
typed into the Render dashboard (the same values are in `apps/web/nginx.conf`). See
[docs/branching-and-environments.md](docs/branching-and-environments.md) and
[docs/security-headers-and-a11y.md](docs/security-headers-and-a11y.md). There is no separate production
environment (a decision to avoid a second paid database); the free staging database expires 30 days
after it was created.

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
- **English only.** A message mostly in a non-Latin script (Hindi, Marathi, Tamil, Arabic and so
  on) is escalated by plain code. Another language written in Latin letters ("mera order nahi
  aaya", French) is not detected: it escalates only if the model is unsure or finds no workflow,
  so a confident model could draft a reply to it.
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
- **Messages about two topics.** Policy passages come from the one intent the classifier picks plus
  the closest by meaning. In the real-embedding check, the secondary topic's passage was missed in
  both tickets that failed (T007, T030).
- **No data clean-up yet.** `RETENTION_DAYS` is a setting, but nothing deletes old data.

## License

[MIT](LICENSE).
