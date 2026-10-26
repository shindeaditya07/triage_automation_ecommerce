# ReplyDesk: Build Plan

**Version:** 1.1 (2026-10-15)
**Spec reference:** `spec.md` v1.1
**Status:** Active. This document schedules and explains the build. Spec and policies are the source of truth for behavior; this document explains rationale and order of work.

---

## Overview

ReplyDesk is built in five phases. Each phase has a clear deliverable and a `make` target that validates it. No phase begins until CI passes for the previous phase. The eval harness is built in Phase 1 so every subsequent change is measured against it.

**Core principle:** build the thing that can tell you if you are right before building the thing you want to be right. The grounding validator, escalation rules, and eval harness are built before the LLM drafting pipeline.

---

## Repository Layout

```
second_e2e_app/
├── apps/
│   └── web/                  # React + TypeScript frontend
│       ├── src/
│       │   ├── components/   # Ticket queue, approval screen, dashboard
│       │   ├── pages/        # Route-level pages
│       │   ├── api/          # Generated types (openapi-typescript)
│       │   ├── hooks/        # TanStack Query hooks
│       │   └── store/        # Auth context only (no server state)
│       ├── package.json
│       └── tsconfig.json
├── services/
│   ├── api/                  # FastAPI backend
│   │   ├── alembic/          # Database migrations
│   │   ├── prompts/          # Versioned prompt files (hashed)
│   │   ├── grounding/        # Deterministic validator + LLM judge
│   │   ├── escalation/       # Two-stage escalation rules engine
│   │   ├── pipeline/         # Intake, classify, context, retrieve, draft
│   │   ├── llm/              # LLMClient interface + provider adapters
│   │   ├── models/           # Pydantic models (ticket, draft, config)
│   │   ├── jobs/             # DB-backed job table worker
│   │   ├── settings.py       # Typed Settings (pydantic-settings)
│   │   └── main.py           # FastAPI app, lifespan, health endpoints
│   └── mock-store/           # FastAPI mock store, seeded edge-case data
│       ├── seed/             # Seed scripts and edge-case data files
│       └── main.py
├── evals/
│   ├── datasets/             # Labeled tickets (JSONL), bad-draft corpus
│   ├── urgency-rubric.md     # Human rubric for urgency labels
│   ├── runner.py             # Eval runner (no live LLM calls in CI)
│   └── results/              # One directory per run: YYYY-MM-DD-<run-id>/
├── docs/
│   ├── build-plan.md         # This file
│   ├── data-contract.md      # Order, item, refund, damage, courier schemas
│   ├── runbook.md            # Operations: what can go wrong and how to fix it
│   ├── spec-amendments.md    # Archived amendments (A1-A19, incorporated in v1.1)
│   └── adr/
│       ├── 0001-concurrency.md
│       ├── 0002-job-runner.md
│       └── 0003-llm-provider.md
├── policy_constants.yaml     # Single source for all numeric policy constants
├── .github/
│   └── workflows/
│       ├── ci.yml            # PR validation
│       ├── staging.yml       # Deploy to staging on merge to main
│       └── release.yml       # Deploy to production on tag
├── docker-compose.yml        # db, api, mock-store, web (local dev)
├── docker-compose.staging.yml
├── docker-compose.prod.yml
├── Makefile                  # Single entry point for all dev tasks
├── .env.example              # Complete, commented list of all env vars
└── README.md
```

---

## Makefile Targets

| Target | What it does |
|---|---|
| `make up` | `docker compose up` — full local stack |
| `make down` | Stop and remove containers |
| `make migrate` | Run Alembic migrations (separate from startup) |
| `make seed` | Seed the mock store with all 31 edge cases |
| `make lint` | ruff + mypy (backend), eslint + prettier (frontend) |
| `make test` | Unit and pipeline tests (no live LLM or network) |
| `make eval` | Full eval run against the labeled dataset (requires live LLM) |
| `make eval-smoke` | Smoke eval against recorded fixtures (CI-safe, no live LLM) |
| `make resilience` | Resilience test: DB stop/start, API kill mid-processing |
| `make build` | Build all Docker images |
| `make deploy-staging` | Push to staging environment |
| `make deploy-prod` | Push to production (requires tag and approval) |
| `make audit` | `pip-audit` + `npm audit` |
| `make policy-check` | Verify policy_constants.yaml matches policies.md passages |

---

## Phase 1 — Foundation and Evaluation Harness

**Duration:** Week 1  
**Validation:** `make lint && make test && make eval-smoke`

### Goal

Build everything that lets you measure if the system is correct *before* building the LLM pipeline. A system that cannot measure itself cannot improve.

### Deliverables

**1.1 Repository structure**
- Create the full directory layout above.
- Initialize Python environment with `uv`, pin dependencies in `uv.lock`.
- Initialize Node with `npm`, pin `package-lock.json`.
- `docker-compose.yml` brings up `db` (Postgres + pgvector), `api` (stub), `mock-store`, and `web` (stub).
- `.env.example` with all required variables documented and commented.
- Pre-commit hooks: ruff, mypy (changed files), eslint, prettier, secret scanner (detect-secrets).

**1.2 Data contract**
- Write `docs/data-contract.md`: full schema for order, item, refund record, damage report, courier events, and customer history.
- Write `policy_constants.yaml` keyed by policy ID.
- Write the CI test that asserts every constant in `policy_constants.yaml` appears in its cited passage of `policies.md` and that the SHA-256 of `policies.md` matches the lock file.

**1.3 Mock store**
- FastAPI service using the data contract from 1.2.
- 31 seeded edge cases (spec section 17) with dates generated relative to a frozen clock.
- Endpoints: `GET /orders/{order_id}?email=...`, `GET /customers/{email}/history`, `GET /health`.
- Idempotent seed script: `make seed`.
- Unit tests for all 31 edge cases using the frozen clock.

**1.4 Ticket and draft schemas**
- Pydantic v2 models for: `TicketCreateRequest`, `Ticket`, `DraftOutput`, `Citation`, `Classification`, `Attachment`, `EscalationResult`.
- JSON Schema exported from Pydantic models — these are the schemas used for LLM structured output and for OpenAPI. Schema and validator cannot drift.
- Alembic migration `0001_initial`: creates `tickets`, `drafts`, `audit_log`, `jobs`, `order_snapshots`, `policy_embeddings` tables with database-level constraints (unique `external_message_id`, enum checks, foreign keys, non-null enforcement).

**1.5 Keyword baseline classifier**
- Deterministic keyword/regex classifier for all 10 intents.
- Serves as the reference to beat in evals.
- Also used as the deterministic escalation backstop (legal, chargeback, abusive keywords — runs alongside the LLM classifier, not after it).

**1.6 Eval harness**
- 60–100 labeled synthetic tickets in `evals/datasets/labeled.jsonl`.
- Ground truth per ticket: intent, urgency, expected escalation, applicable policy IDs.
- `evals/urgency-rubric.md` with clear criteria for each urgency level.
- Seeded bad-draft corpus: `evals/datasets/bad_drafts.jsonl` (start with 30+ known violations; grow to 100+ before v1 release).
- `evals/runner.py`: uses a fake `LLMClient` returning recorded outputs; no live LLM in CI.
- `make eval-smoke` runs the harness against recorded fixtures and reports per-intent precision/recall, escalation recall, and grounding failure rate with Wilson confidence intervals.
- Policy-ID coverage matrix check: CI fails if any policy ID in a label is absent from `policies.md`.

**Rationale for phase ordering:** the eval harness must exist before any LLM call is made, so every change from Phase 2 onward is immediately measurable.

---

## Phase 2 — Intake, Classification, Context, and Policy Retrieval

**Duration:** Week 2  
**Validation:** `make lint && make test && make eval-smoke` (classification metrics must appear in report)

### Goal

Build the deterministic pipeline stages and the LLM classification step. By end of phase, tickets can be ingested, classified, and their escalation determined — without drafting.

### Deliverables

**2.1 Intake pipeline**
- `POST /api/v1/tickets/form` (public): rate limiting (per-IP, per-hour), body size limit, honeypot field, input sanitization (strip HTML, links, hidden text), PII masking (card numbers, bank accounts, UPI IDs, govt-ID patterns).
- `POST /api/v1/tickets/email` (HMAC-authenticated): HMAC-SHA256 signature validation.
- Both endpoints normalize to `Ticket`; idempotent on `external_message_id`; threading via `in_reply_to`.
- Attach metadata only for attachments; set `has_photo` flag.
- Enqueue the ticket to the DB-backed job table.

**2.2 DB-backed job runner**
- `jobs` table: `job_id`, `ticket_id`, `state` (pending/claimed/done/failed), `locked_at`, `attempts`.
- Worker loop: `SELECT ... FOR UPDATE SKIP LOCKED`; processes one job at a time per worker.
- Startup sweeper: re-queues jobs locked > `lock_timeout_seconds` at startup.
- Periodic sweeper: runs every `sweep_interval_seconds`; re-queues stale locked jobs.
- Every stage is idempotent: draft generation keyed by `ticket_id + input_hash`.
- Record ADR: `docs/adr/0002-job-runner.md`.

**2.3 LLM client interface**
- `LLMClient` abstract interface: `classify(ticket) → ClassificationOutput`, `draft(context) → DraftOutput`, `judge(draft, context) → JudgeOutput`, `embed(texts) → list[list[float]]`.
- Provider adapter for chosen LLM (record in `docs/adr/0003-llm-provider.md`).
- `FakeLLMClient` returning recorded outputs for CI.
- Prompt files in `services/api/prompts/`, each hashed; version and hash recorded per call.
- Token counts, latency, and cost in INR recorded per call. Price schedule in config.

**2.4 Classification**
- LLM call (temperature=0) returning structured `ClassificationOutput` validated by Pydantic.
- Intent enum: `order_status | refund | return | damaged_item | product_question | cancellation | address_change | payment | warranty | other`.
- Flags from classifier: `angry | abusive | legal_threat | suspected_fraud | prompt_injection_suspected | repeat_complaint | order_mismatch | compensation_request`. (`high_value` is NOT set here.)
- Invalid output is retried once with the validation error, then the ticket is escalated.

**2.5 Pre-lookup escalation**
- Deterministic rules engine in `services/api/escalation/pre_lookup.py`.
- Conditions (OR): classifier flags `abusive`, `legal_threat`, `suspected_fraud`, `prompt_injection_suspected` **OR** keyword/regex backstop matches **OR** confidence < 0.70 **OR** intent = `other`.
- Escalated tickets get `status = "escalated"`, `escalation_reason` set, job marked done; no draft is attempted.

**2.6 Order context lookup**
- Extract order ID from message text (regex) or match by sender email.
- Call mock store: `GET /orders/{order_id}?email={sender_email}`.
- On mismatch: set flag `order_mismatch`, escalate (post-lookup). No order data enters context.
- On not-found: escalate (post-lookup).
- Snapshot the order to `order_snapshots` table; every draft references the snapshot.

**2.7 Post-lookup escalation**
- Deterministic rules engine in `services/api/escalation/post_lookup.py`.
- No-draft cases: `order_mismatch`, `order_not_found`, `repeat_complaint` (3+ messages on thread), `high_value` (order_value > threshold).
- `high_value` flag is set HERE by the rules engine after reading the order value.
- Suggestion-only draft cases: outside-policy requests under ESC-8.3.

**2.8 Policy retrieval (pgvector)**
- Embed `policies.md` passages and FAQ on startup (or on demand if policy version changed).
- Store embeddings in `policy_embeddings` table with passage ID, content, and policy version.
- `/readyz` fails if loaded policy version ≠ `policies.md` version header.
- Retrieval: top-k vector results + deterministic passages by intent (e.g., `damaged_item` always includes DMG-3.1, DMG-3.4).

**2.9 Health endpoints**
- `GET /healthz`: liveness; never touches DB; always returns 200 while process is up.
- `GET /readyz`: readiness; checks DB (`SELECT 1`), pgvector extension, migrations at head, policy index loaded and version-matched; returns 503 with JSON body naming the failing component.

**2.10 Owner auth**
- Single owner account. Argon2 password hash. Session: JWT in httpOnly/Secure/SameSite=Strict cookie.
- `POST /api/v1/auth/login`, `POST /api/v1/auth/logout`, `POST /api/v1/auth/refresh`.
- CSRF token for all cookie-authenticated mutations.
- Brute-force protection: rate limit on `/login`, lock out after N failures.

---

## Phase 3 — Drafting, Grounding Validator, and Escalation Rules

**Duration:** Week 3  
**Validation:** `make lint && make test && make eval-smoke` (grounding failure rate must appear in report; must catch 100% of seeded bad drafts)

### Goal

Build the LLM drafting step and the grounding validator. The validator must be the most heavily tested module in the codebase.

### Deliverables

**3.1 Drafting**
- LLM call (structured output) with only retrieved order snapshot and policy passages in context; no other data.
- Customer text in a delimited data block `<customer_message>...</customer_message>`.
- Delimiter neutralization for any `<customer_message>` strings found in customer text.
- Output: `DraftOutput` (section 12 of spec); validated by Pydantic.
- Store inputs alongside output (order snapshot ID, passage IDs, prompt hash, model, policy version).

**3.2 Grounding validator — deterministic**
- `services/api/grounding/validator.py`: pure function, input → structured failure list.
- Rules G-1 through G-10 (spec section 13.1), each as a named, independently testable function.
- Commitment phrase list read from config (not hard-coded in Python).
- Store profile (name, sign-off, hours, timezone, currency) read from config.
- Output: `ValidationResult` with list of `RuleFailure(rule_id, span, citation_missing, detail)`.
- **Target:** ≥ 95% branch coverage on `services/api/grounding/`. CI enforces this.

**3.3 Grounding validator — LLM judge**
- `services/api/grounding/judge.py`: calls judge model (different family from drafter); returns `JudgeOutput(pass: bool, issues: list[str], confidence: float)`.
- Can only add blocks; cannot override a deterministic failure.
- Result stored in audit log.

**3.4 Retry and fallback**
- On failure: inject structured `ValidationResult` into prompt as additional context, retry once.
- On second failure: use safe fallback template (stored in config), set ticket flag `used_fallback_template`, add to approval queue with notice to owner.

**3.5 Owner-edit validation**
- Re-run `validator.py` on owner-edited text before the owner can save.
- Return `ValidationResult` as a non-blocking warning to the UI.
- If the owner proceeds: record `EditOverride(reason, overridden_rules, actor, timestamp)` in the audit log.

**3.6 Grounding tests**
- Positive fixtures: 20+ valid drafts that the validator must pass; all 31 edge cases from the mock store.
- Adversarial / bad-draft corpus: 100+ drafts with known violations; every one must be caught (zero misses).
- Tone rule tests: sign-off, apology count, length limit.
- Injection echo tests: drafts that appear to obey injected instructions.

**3.7 Escalation integration tests**
- Pipeline tests with fake LLMClient: verify that pre-lookup and post-lookup escalation triggers fire correctly for all 31 edge cases.
- Verify no-draft path for no-draft escalation cases.
- Verify suggestion-only draft path for ESC-8.3 cases.

---

## Phase 4 — Approval UI, Metrics Dashboard, and Audit Log

**Duration:** Week 4  
**Validation:** `make lint && make test && make eval-smoke`; manual UI review against all 31 edge cases

### Goal

Build the frontend and the metrics pipeline. The product is complete when the owner can manage the full ticket lifecycle from the UI.

### Deliverables

**4.1 Ticket queue**
- Sorted by urgency (critical → high → normal → low).
- Visual distinction for escalated tickets and suspected-injection tickets.
- Intent badge, urgency badge, escalation reason, age.
- Empty state, loading state, error state, "server waking up" state (after N seconds without response).

**4.2 Approval screen**
- Side-by-side layout: original customer message | draft reply | evidence panel.
- Evidence panel: cited order fields, cited policy passages (with full text), flags, grounding result (rules passed/failed), confidence score.
- Escalated / suspected-injection tickets show original message and cannot be one-click approved without explicitly viewing it.
- Suggestion-only drafts are labeled prominently; approve button requires extra confirmation step.
- Edit: inline editor, updates `reply_text`; on save, re-runs client-side grounding summary (non-blocking); submits `EditOverride.reason` if any warning was overridden.
- Approve, reject buttons. Reject requires a reason (stored in audit log).
- Keyboard: A = approve, R = reject, E = edit, focus-trapped in dialogs.

**4.3 Metrics dashboard**
- All metrics from spec section 18, computed from stored data.
- Time range picker: last 7 days / 30 days / custom.
- Grounding failures: bar chart by rule (G-1 through G-10).
- Intent and urgency breakdown: stacked bar.
- Cost per ticket: line chart over time.
- `time_to_draft` and `time_to_send` distributions (p50, p95).
- Export dashboard data as CSV.

**4.4 Audit log view**
- Paginated list of all events for a ticket (selectable by ticket ID).
- Events: ingested, classified, escalated (with reason), draft created, grounding pass/fail details, owner approved/rejected (with reason), edit diff, mock sent, fallback used.

**4.5 API types**
- Generate TypeScript types from FastAPI OpenAPI schema using `openapi-typescript`. Never hand-write response types.
- TanStack Query hooks for all data views. No ad-hoc `useEffect` fetching.
- No tokens in `localStorage`. No secrets in the bundle.

**4.6 Security headers and accessibility**
- CSP, X-Content-Type-Options, X-Frame-Options, Referrer-Policy set by the reverse proxy or the API.
- No `dangerouslySetInnerHTML` on customer or model text.
- Semantic HTML, ARIA labels, sufficient contrast, keyboard navigation for all interactive elements.

---

## Phase 5 — Production Hardening, Full Eval, and Release

**Duration:** Week 5  
**Validation:** `make lint && make test && make eval && make resilience`; deploy to staging; external uptime monitor active

### Goal

Prove correctness on the full labeled set, prove resilience, deploy to production, and document everything.

### Deliverables

**5.1 Full eval run**
- `make eval` against the full 60–100 labeled ticket set using the live LLM.
- Report: per-intent precision/recall (with Wilson CI), escalation recall, grounding failure rate, draft acceptance rate, `time_to_draft`, `time_to_send`, cost per ticket.
- All numbers committed to `evals/results/YYYY-MM-DD-<run-id>/`.
- Policy-ID coverage matrix verified: every policy ID exercised.
- All seeded bad drafts caught by the validator (100% recall on bad-draft corpus).

**5.2 Resilience test**
`make resilience` runs this sequence automatically:
1. Start the stack; `GET /readyz` returns 200.
2. `docker compose stop db`; `GET /readyz` returns 503; `GET /healthz` returns 200.
3. `docker compose start db`; `GET /readyz` recovers to 200 without restarting the API process.
4. Submit a ticket, kill the API mid-processing; restart the API; sweeper re-queues; verify no duplicate draft; verify ticket reaches a terminal state (`drafted`, `escalated`, or `failed`).

**5.3 End-to-end pipeline test**
- `make test` includes a full pipeline test: ingest → classify → escalation check → context → retrieval → draft → grounding → approval queue, using the fake LLMClient and the mock store.
- Covers all 31 edge cases.

**5.4 Staging deployment**
- Deploy to staging environment via `make deploy-staging` (GitHub Actions `staging.yml` on merge to `main`).
- Confirm `GET /readyz` returns 200 on staging.
- Run smoke eval against staging.
- Owner manually reviews 5 tickets from each intent category.

**5.5 Production deployment**
- Tag the release: `git tag v1.0.0`.
- GitHub Actions `release.yml` builds and pushes to production.
- External uptime monitor (e.g., Better Uptime, UptimeRobot) configured on `/readyz` with email alerts.
- Verify production `/readyz` returns 200.
- Run synthetic check: submit seeded test ticket, expect draft or escalation within 30 seconds.

**5.6 Documentation**
- `README.md`: what it is, architecture diagram, `docker compose up` instructions, eval results table (date, model, prompt hashes), short demo GIF or video, known limitations, deployment notes.
- `docs/runbook.md`: what can go down, how it shows up in logs, how to recover, external monitor setup, free-tier limits (if any).
- `docs/adr/0001-concurrency.md`: async FastAPI + sync SQLAlchemy with a thread pool; rationale.
- `docs/adr/0002-job-runner.md`: DB-backed job table instead of FastAPI BackgroundTasks; rationale.
- `docs/adr/0003-llm-provider.md`: chosen provider, judge model, why different families.
- Known limitations honestly documented.

---

## CI/CD Pipeline Detail

### GitHub Actions: `ci.yml` (runs on every PR)

```yaml
jobs:
  lint-backend:
    - ruff check services/ evals/
    - ruff format --check services/ evals/
    - mypy services/api services/mock-store evals/

  lint-frontend:
    - eslint apps/web/src
    - prettier --check apps/web/src

  secret-scan:
    - detect-secrets scan (fail on new secrets)

  test-backend:
    - spin up Postgres with pgvector (GitHub Actions service)
    - alembic upgrade head
    - pytest services/api/tests/ --cov=services/api/grounding --cov-fail-under=95
    - pytest services/mock-store/tests/

  test-frontend:
    - npm test (vitest)
    - npm run build (type check + bundle)

  migration-check:
    - spin up empty Postgres
    - alembic upgrade head (must succeed from empty)
    - alembic downgrade -1 + upgrade head again (reversibility)

  dependency-audit:
    - pip-audit --fail-on-vulnerable
    - npm audit --audit-level=high

  policy-check:
    - python scripts/check_policy_constants.py (assert constants match policies.md passages)
    - python scripts/check_policy_sha.py (assert SHA-256 lock matches)

  eval-smoke:
    - make eval-smoke (fake LLMClient, recorded fixtures)
    - Fail if escalation recall < 0.95 on smoke set
    - Fail if any seeded bad draft passes the validator

  container-build:
    - docker build services/api
    - docker build services/mock-store
    - docker build apps/web
```

### GitHub Actions: `staging.yml` (runs on merge to `main`)

- All CI jobs above (using production-grade images).
- Push images to container registry (tagged `main-<sha>`).
- Deploy to staging using Docker Compose or Render deploy hook.
- Run `make eval-smoke` against staging API.
- Notify in Slack/email on success or failure.

### GitHub Actions: `release.yml` (runs on `v*` tag push)

- Require CI to be green on the tagged commit.
- Build images tagged with the version (e.g., `v1.0.0`).
- Push to container registry.
- Deploy to production.
- Run synthetic check: POST test ticket, expect response within 30 s.
- Notify on success or failure.

---

## Deployment Architecture

```
[Cloudflare / reverse proxy]
  ├── / → [Static frontend (CDN)]
  └── /api → [FastAPI API service (Render / Docker)]
                └── [Postgres + pgvector (Render managed DB)]
                └── [Mock store service (same Docker network)]

[External uptime monitor] → /readyz (email alert on 503)
[GitHub Actions]          → CI, staging deploy, release deploy
```

**Environment variables** govern all differences between local, staging, and production. See `.env.example`.

**Secrets** live only in environment variables or a secrets manager (Render environment, or AWS Secrets Manager for stretch goal). Never in code, never in the image.

**Multi-stage Docker builds:** `builder` stage installs deps; `runtime` stage is lean, runs as non-root, has no dev tools, uses pinned base image tags.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM provider outage | Medium | High | Circuit breaker; degraded to escalation; synthetic monitor alerts within 5 min |
| DB connection drops (idle) | Medium | High | `pool_pre_ping`, `pool_recycle=1800`; resilience test validates recovery |
| Prompt injection in customer message | Low | Critical | Delimited data blocks; injection detection; flag and escalate; UI shows original |
| Policy constant drift (policies.md edited without bump) | Low | High | SHA-256 lock file; CI fails immediately |
| LLM grounding failure (hallucinated fact reaches queue) | Medium | High | Deterministic validator blocks first; grounding eval measures rate; near 0% target |
| Free-tier sleep (if used) | Medium | Medium | Document in runbook; "server waking up" UI state; do not use free tier for demo path |
| COD order above cap submitted | Low | Medium | Validator check; blocked at ingestion |

---

## Open Items at Build Start

1. Confirm LLM provider and judge model family → record in ADR 0003 before Phase 2.
2. Provide India 2026 public holiday list for business-day calculations → needed before Phase 2 clock tests.
3. Confirm `product_question` v1 scope: FAQ-only, escalate everything else (spec section 23).
4. DPDP Act compliance review for real-customer deployment (out of scope for demo; document limitation).
5. Confirm staging and production hosting targets (Render recommended; AWS as stretch).
