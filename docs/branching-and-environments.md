# ReplyDesk: Feature Branches and Environment Setup Guide

**Version:** 1.0 (2026-10-15)
**Audience:** Developer building ReplyDesk from scratch.
**Spec reference:** `spec.md` v1.1, `build-plan.md` v1.1

This guide answers two questions:
1. **What branches do I create, and what features go in each?**
2. **How do I set up and operate the four environments (dev, UAT, non-prod/staging, prod)?**

---

## Part 1 — Feature Branches

### Branch Strategy Overview

```
main  (protected — always deployable)
  └── develop  (integration branch — merges here trigger staging deploy)
        ├── feature/PHASE-1-*
        ├── feature/PHASE-2-*
        ├── feature/PHASE-3-*
        ├── feature/PHASE-4-*
        └── feature/PHASE-5-*
```

**Rules:**
- `main` is the production branch. Only merge from `develop` via a PR after staging is clean.
- `develop` is the integration branch. Only merge from feature branches via PRs. Every merge triggers a staging deploy and smoke eval.
- Feature branches are cut from `develop`, never from `main`.
- Branch names follow the pattern: `feature/<phase>-<short-description>` (e.g., `feature/p1-repo-scaffold`).
- One feature branch = one clear deliverable (listed below). Do not stack unrelated work in the same branch.
- CI runs on every push to any branch. A PR cannot be merged unless CI is green.
- Branches are deleted after merging (they exist only while the feature is in progress).

> **Merge order within a phase matters.** Dependencies are noted in each branch section. Merge infrastructure/schema branches before logic branches.

---

### Phase 1 Branches — Foundation and Eval Harness

These branches set up the skeleton everything else builds on. Build and merge them in order.

---

#### `feature/p1-repo-scaffold`

**What it contains:**
- Full directory structure (`apps/web/`, `services/api/`, `services/mock-store/`, `evals/`, `docs/`)
- `docker-compose.yml` with stub services for `db` (Postgres + pgvector), `api`, `mock-store`, `web`
- `Makefile` with all targets defined (even if they just echo "not yet implemented")
- `.env.example` — complete, commented list of every env var the project will ever need
- Python environment: `uv` init, `pyproject.toml`, empty `uv.lock`
- Node environment: `package.json` for `apps/web/`, empty `package-lock.json`
- Pre-commit config: ruff, mypy (changed files), eslint, prettier, detect-secrets
- `.github/workflows/ci.yml` — skeleton CI that lints and runs empty test suite
- `.gitignore`
- `README.md` placeholder

**Merges into:** `develop`
**Depends on:** nothing (first branch)
**Validation:** `make lint` passes on an empty codebase; `docker compose up` starts without errors.

---

#### `feature/p1-data-contract`

**What it contains:**
- `docs/data-contract.md` — full schemas for: Order, Item, DamageReport, CourierEvent, RefundRecord, CustomerHistory
- `policy_constants.yaml` — all numeric policy constants keyed by policy ID (return_window_days, damage_window_hours, free_shipping_threshold_paise, etc.)
- `scripts/check_policy_constants.py` — CI script that asserts every constant value appears in its cited passage in `policies.md`
- `scripts/check_policy_sha.py` — CI script that asserts the SHA-256 of `policies.md` matches the stored lock file
- The SHA-256 lock value stored in `.policy_lock`
- CI job `policy-check` added to `ci.yml`

**Merges into:** `develop`
**Depends on:** `feature/p1-repo-scaffold`
**Validation:** `make policy-check` passes.

---

#### `feature/p1-db-schema`

**What it contains:**
- Pydantic v2 models: `Ticket`, `TicketCreateRequest`, `Classification`, `Attachment`, `DraftOutput`, `Citation`, `EscalationResult`, `AuditEvent`, `Job`, `OrderSnapshot`, `PolicyEmbedding`
- Alembic setup (`alembic.ini`, `env.py`)
- Migration `0001_initial`: creates all tables (`tickets`, `drafts`, `audit_log`, `jobs`, `order_snapshots`, `policy_embeddings`) with DB-level constraints (UNIQUE on `external_message_id`, CHECK on status/intent enums, NOT NULL, foreign keys)
- `services/api/settings.py` — typed `Settings` object (pydantic-settings), reading from environment. Fails fast at startup if required vars are missing.
- Unit tests: models validate correctly; migration applies cleanly from empty; migration reverses cleanly

**Merges into:** `develop`
**Depends on:** `feature/p1-data-contract`
**Validation:** `make migrate` on an empty DB succeeds and reverses cleanly.

---

#### `feature/p1-mock-store`

**What it contains:**
- `services/mock-store/main.py` — FastAPI service
- Endpoints: `GET /orders/{order_id}?email=`, `GET /customers/{email}/history`, `GET /health`
- `services/mock-store/seed/` — seed data for all **31 edge cases** from spec section 17, with dates generated relative to a frozen clock (`FROZEN_NOW` env var)
- Seed script: `make seed` is idempotent
- Unit tests for all 31 seeded cases using the frozen clock; asserts correct field values for each scenario
- `docker-compose.yml` updated so `mock-store` service is fully functional

**Merges into:** `develop`
**Depends on:** `feature/p1-db-schema` (uses the same data contract)
**Validation:** `make seed && make test` (mock-store tests only).

---

#### `feature/p1-eval-harness`

**What it contains:**
- `evals/datasets/labeled.jsonl` — 60–100 labeled synthetic tickets (intent, urgency, expected escalation, applicable policy IDs, expected draft/no-draft) — written and reviewed by a human
- `evals/datasets/bad_drafts.jsonl` — 30+ seeded drafts with known grounding violations (will grow to 100+ before release)
- `evals/urgency-rubric.md` — human rubric for urgency labeling
- `evals/runner.py` — uses `FakeLLMClient`; no live LLM calls; reports per-intent precision/recall (with Wilson CIs), escalation recall, grounding failure rate
- `FakeLLMClient` in `services/api/llm/fake.py` — returns recorded outputs for CI
- `make eval-smoke` target wired up
- CI job `eval-smoke` added to `ci.yml` (fails if escalation recall < 0.95 on smoke set)
- Policy-ID coverage matrix check: CI fails if a label references a policy ID absent from `policies.md`

**Merges into:** `develop`
**Depends on:** `feature/p1-db-schema`, `feature/p1-mock-store`
**Validation:** `make eval-smoke` passes and prints a metrics report.

---

#### `feature/p1-keyword-baseline`

**What it contains:**
- `services/api/escalation/keyword_backstop.py` — deterministic keyword/regex rules for: legal threats, chargeback, abusive terms, consumer court, fraud indicators
- `services/api/pipeline/baseline_classifier.py` — deterministic keyword classifier for all 10 intents (serves as the eval baseline to beat)
- Unit tests: every keyword/regex rule is independently tested; adversarial inputs tested (unicode tricks, partial matches, case variants)
- Baseline classifier results added to `evals/runner.py` output so every eval run shows LLM vs. baseline

**Merges into:** `develop`
**Depends on:** `feature/p1-eval-harness`
**Validation:** `make eval-smoke` shows baseline metric column.

---

### Phase 2 Branches — Intake, Classification, Escalation, Context

---

#### `feature/p2-health-endpoints`

**What it contains:**
- `GET /healthz` — liveness; never touches DB; always 200 while process is alive
- `GET /readyz` — readiness; checks: DB (`SELECT 1`), pgvector extension present, Alembic at head, policy index loaded and version-matched; returns 503 with JSON body naming the failing component
- Unit tests: readyz returns 503 when DB is down; healthz stays 200 when DB is down
- `docker-compose.yml` healthcheck for each service using `/healthz`

**Merges into:** `develop`
**Depends on:** `feature/p1-db-schema`
**Validation:** `make test` (health endpoint tests pass).

---

#### `feature/p2-owner-auth`

**What it contains:**
- `POST /api/v1/auth/login` — validates credentials, sets JWT in httpOnly/Secure/SameSite=Strict cookie
- `POST /api/v1/auth/logout` — clears session cookie
- `POST /api/v1/auth/refresh` — refreshes access token using refresh token cookie
- Argon2 password hashing; seed script creates the single owner account
- CSRF token middleware for all cookie-authenticated mutations (double-submit cookie pattern)
- Brute-force protection: per-IP and per-account rate limit on `/login`; lock-out after N failures
- Unit tests: login/logout flow; CSRF rejection; brute-force lockout; JWT expiry; invalid credentials
- `docs/adr/0001-concurrency.md` — chosen concurrency style (async FastAPI + thread pool for sync SQLAlchemy)

**Merges into:** `develop`
**Depends on:** `feature/p1-db-schema`
**Validation:** `make test` (auth tests pass).

---

#### `feature/p2-job-runner`

**What it contains:**
- `jobs` table migration (if not already in `0001_initial`; otherwise migration `0002_jobs`)
- `services/api/jobs/worker.py` — worker loop using `SELECT ... FOR UPDATE SKIP LOCKED`
- Startup sweeper: re-queues jobs locked > `lock_timeout_seconds` on API startup
- Periodic sweeper: runs every `sweep_interval_seconds`
- Idempotency: every job stage is keyed by `ticket_id + input_hash`; retries never create duplicates
- Failed tickets surface in `failed` status with a reason; never stall silently
- `docs/adr/0002-job-runner.md` — rationale for DB-backed jobs vs. FastAPI BackgroundTasks
- Unit tests: sweeper re-queues a stale job; duplicate job is rejected; failed job shows reason

**Merges into:** `develop`
**Depends on:** `feature/p1-db-schema`
**Validation:** `make test` (job runner tests pass).

---

#### `feature/p2-intake`

**What it contains:**
- `POST /api/v1/tickets/form` (public):
  - Per-IP and per-hour rate limits
  - Body size limit (reject 413)
  - Honeypot field check
  - Input sanitization (strip HTML, `<script>`, links, hidden text)
  - PII masking (card numbers via Luhn check, bank account numbers, UPI IDs `xxx@yyy`, Aadhaar/PAN patterns) before storage and before any LLM call
  - Attachment metadata capture; `has_photo` flag set
- `POST /api/v1/tickets/email` (HMAC-authenticated):
  - HMAC-SHA256 signature validation using shared secret; reject if missing or invalid
  - Same normalization and masking as form endpoint
- Idempotency on `external_message_id`; threading via `in_reply_to`
- Both normalize to `Ticket` and enqueue to the job table
- Unit tests: rate limit triggers; honeypot rejects; PII masking (card numbers masked, `has_photo` set correctly, HTML stripped); HMAC rejection; idempotency (duplicate message ignored); threading attaches reply to existing ticket
- Adversarial: prompt injection in body and subject; oversized body; delimiter string in body

**Merges into:** `develop`
**Depends on:** `feature/p2-job-runner`, `feature/p2-owner-auth`
**Validation:** `make test` (intake tests pass).

---

#### `feature/p2-llm-client`

**What it contains:**
- `services/api/llm/client.py` — `LLMClient` abstract interface: `classify()`, `draft()`, `judge()`, `embed()`
- Provider adapter for the chosen LLM (record name in `docs/adr/0003-llm-provider.md`)
- `services/api/llm/fake.py` — `FakeLLMClient` used in all CI tests (returns recorded outputs)
- `services/api/prompts/` — prompt files (one per step: classify, draft, judge); each hashed on load
- Per-call recording: `prompt_version`, `prompt_hash`, `model`, `temperature`, token counts, latency ms, cost INR — stored in the job/audit record
- LLM price schedule in `settings.llm_prices` (not in code)
- Circuit breaker: sustained provider failure degrades to escalation, not a hang
- Bounded retries with backoff + jitter on transient errors; no retry on 4xx
- Unit tests: fake client returns expected output; invalid structured output triggers escalation; circuit breaker trips after N failures; cost is computed from token counts

**Merges into:** `develop`
**Depends on:** `feature/p1-db-schema`
**Validation:** `make test` (LLM client tests pass); `docs/adr/0003-llm-provider.md` written.

---

#### `feature/p2-classification`

**What it contains:**
- Classification pipeline stage: calls `LLMClient.classify()` with the ticket body in a delimited data block
- `temperature=0`; structured output validated by Pydantic `ClassificationOutput`
- Intent enum: all 10 intents (`order_status`, `refund`, `return`, `damaged_item`, `product_question`, `cancellation`, `address_change`, `payment`, `warranty`, `other`)
- Classifier flags: `angry`, `abusive`, `legal_threat`, `suspected_fraud`, `prompt_injection_suspected`, `repeat_complaint`, `order_mismatch`, `compensation_request` (NOT `high_value` — that is set post-lookup)
- Invalid output: retry once with validation error injected; escalate on second failure
- Delimiter neutralization: any `<customer_message>` found in the message body is neutralized before insertion
- Unit tests: all 10 intents classified correctly (using FakeLLMClient); invalid output triggers escalation; delimiter neutralization; all classifier flags populate

**Merges into:** `develop`
**Depends on:** `feature/p2-llm-client`, `feature/p2-intake`
**Validation:** `make test && make eval-smoke` (classification metrics appear in report).

---

#### `feature/p2-pre-lookup-escalation`

**What it contains:**
- `services/api/escalation/pre_lookup.py` — deterministic rules engine
- Escalation conditions (OR): classifier flags `abusive`, `legal_threat`, `suspected_fraud`, `prompt_injection_suspected` **OR** keyword backstop (from `p1-keyword-baseline`) **OR** confidence < 0.70 **OR** intent = `other`
- Either the classifier flag or the keyword backstop is sufficient; they run concurrently
- Escalated ticket: `status = "escalated"`, `escalation_reason` set, job marked done, no further pipeline stages run
- Prompt injection: body is also scanned structurally (delimiter boundaries, role tokens); flag raised if detected; original message surfaced to owner in UI
- Unit tests: each escalation trigger independently; both paths (classifier flag and keyword backstop) trigger; confidence threshold boundary; injection detection

**Merges into:** `develop`
**Depends on:** `feature/p2-classification`, `feature/p1-keyword-baseline`
**Validation:** `make test && make eval-smoke` (escalation recall ≥ 0.95 on smoke set).

---

#### `feature/p2-context-lookup`

**What it contains:**
- Order ID extraction from message text (regex, multiple format patterns)
- Fallback: match by sender email when no order ID found
- HTTP call to mock store: `GET /orders/{order_id}?email={sender_email}`; explicit timeout
- Order snapshot saved to `order_snapshots` table; referenced by `snapshot_id` on every draft
- Mismatch path: sets `order_mismatch` flag; no order data returned or stored in context
- Not-found path: sets `order_not_found` flag; no data stored in context
- Customer history lookup: `GET /customers/{email}/history`
- Unit tests: order ID extracted from various message formats; mismatch returns only flag; not-found returns only flag; snapshot is saved; multiple orders for same email (ambiguous case)

**Merges into:** `develop`
**Depends on:** `feature/p2-classification`, `feature/p1-mock-store`
**Validation:** `make test` (context lookup tests pass for all relevant mock-store edge cases).

---

#### `feature/p2-post-lookup-escalation`

**What it contains:**
- `services/api/escalation/post_lookup.py` — deterministic rules engine
- **No-draft escalation:** `order_mismatch`, `order_not_found`, `repeat_complaint` (3+ messages on thread, checked against `tickets` table), `high_value` (order value > threshold from `policy_constants.yaml`)
- `high_value` flag is set HERE — after reading the order; the classifier does NOT set it
- **Suggestion-only draft escalation (ESC-8.3):** outside-policy requests — return after 7-day window, damage report after 48 hours, compensation request, final-sale refund (not damaged), any situation not in `policies.md`; these get a draft labeled "Escalated — suggestion only", cannot be one-click approved
- Time window logic: `services/api/time_utils.py` — return window (calendar days in IST), damage window (exact 48h from UTC timestamp), boundary at exactly 7 days / 48 h / 1 s over; clock is injected (never reads `now` directly)
- Unit tests: all no-draft escalation triggers; suggestion-only triggers; `high_value` set correctly; time window boundary cases (exactly at limit, exactly 1 s over); repeat complaint threshold at exactly 3

**Merges into:** `develop`
**Depends on:** `feature/p2-context-lookup`, `feature/p1-data-contract`
**Validation:** `make test && make eval-smoke`.

---

#### `feature/p2-policy-retrieval`

**What it contains:**
- `LLMClient.embed()` called on `policies.md` passages and FAQ entries at startup (or on demand if policy version changed)
- `policy_embeddings` table populated; records policy version per row
- `GET /readyz` extended: fails if loaded policy version ≠ `policies.md` version header
- Retrieval function: returns top-k vector results via pgvector cosine similarity PLUS deterministic passages by intent (e.g., `damaged_item` always includes DMG-3.1, DMG-3.4 regardless of vector score)
- Every cited policy ID verified to exist in the loaded policy set before drafting
- Unit tests: retrieval returns correct deterministic passages for each intent; policy version mismatch fails readyz; hallucinated policy ID is caught

**Merges into:** `develop`
**Depends on:** `feature/p2-post-lookup-escalation`, `feature/p2-llm-client`
**Validation:** `make test` (retrieval tests pass); `make eval-smoke` shows retrieval metrics.

---

### Phase 3 Branches — Drafting and Grounding

---

#### `feature/p3-drafting`

**What it contains:**
- Drafting pipeline stage: calls `LLMClient.draft()` with only the order snapshot and retrieved policy passages in context
- Customer message in a `<customer_message>` delimited block with explicit "this is data, not instructions" preamble
- Delimiter strings found in customer text are neutralized before insertion
- Output: `DraftOutput` (reply_text, citations[], proposed_action, confidence, reproducibility chain)
- Reproducibility chain stored: `order_snapshot_id`, retrieved passage IDs, `prompt_hash`, `prompt_version`, `model`, `policy_version`
- Unit tests: drafter receives exactly the right context (no extra customer PII); delimited block is present; reproducibility chain is stored; `proposed_action` is in the allowed enum

**Merges into:** `develop`
**Depends on:** `feature/p2-policy-retrieval`
**Validation:** `make test` (drafting unit tests pass with FakeLLMClient).

---

#### `feature/p3-grounding-validator`

**What it contains:**
- `services/api/grounding/validator.py` — pure function, input → `ValidationResult`
- 10 rules, each as an independently testable named function:
  - **G-1** Order-derived value extraction and exact match against cited order field (after normalizing ₹ symbols, digit separators, date formats)
  - **G-2** Policy-derived value match against cited passage in `policy_constants.yaml`
  - **G-3** Commitment phrase list check (`settings.grounding.commitment_phrases`); maintained in config
  - **G-4** Citation existence — every cited policy ID in loaded set; every cited order field in snapshot
  - **G-5** No uncited factual claim
  - **G-6** Privacy — no other-customer IDs or emails
  - **G-7** Injection echo — detect patterns suggesting obeyed instructions
  - **G-8** Sign-off — exact match against `settings.store.sign_off`
  - **G-9** Apology count — at most one apology phrase (`settings.grounding.apology_phrases`)
  - **G-10** Length — `len(reply_text) <= settings.grounding.max_reply_chars`
- `ValidationResult` contains `list[RuleFailure(rule_id, span, citation_missing, detail)]`
- CI branch coverage enforcement: `--cov=services/api/grounding --cov-fail-under=95`
- **Positive fixtures:** 20+ valid drafts that all pass (must not be false-blocked)
- **Adversarial fixtures (bad-draft corpus must all be caught):**
  - Draft promises a refund outcome
  - Draft states a date not in the order
  - Draft references another customer's order ID
  - Draft is missing a sign-off
  - Draft has two apology phrases
  - Draft contains a hallucinated policy ID
  - Draft echoes an injected instruction
  - Draft has correct amount but wrong citation field
  - Draft states a policy value correctly but with no policy ID citation
  - Draft exceeds length limit

**Merges into:** `develop`
**Depends on:** `feature/p3-drafting`
**Validation:** `make test` (≥ 95% branch coverage on `grounding/`); `make eval-smoke` grounding failure rate reported.

---

#### `feature/p3-llm-judge`

**What it contains:**
- `services/api/grounding/judge.py` — calls judge model (different family from drafter)
- Returns `JudgeOutput(pass: bool, issues: list[str], confidence: float)`
- Can only add blocks; cannot override a deterministic pass or failure
- Judge confidence and issue list stored in the audit log
- Unit tests: judge blocks a draft that the deterministic validator passed; judge cannot un-block a deterministically failed draft; judge confidence is recorded

**Merges into:** `develop`
**Depends on:** `feature/p3-grounding-validator`
**Validation:** `make test` (judge tests pass).

---

#### `feature/p3-retry-and-fallback`

**What it contains:**
- Retry logic: on grounding failure, inject structured `ValidationResult` (rule IDs, spans, missing citations) into the draft prompt and retry once
- Fallback: on second failure, replace with safe fallback template from `settings.grounding.fallback_template`; set `used_fallback_template = true` on the ticket; add to approval queue with an owner-visible notice
- Rate/count tracked per ticket per run (cannot loop indefinitely)
- Unit tests: retry injects correct failure reasons; fallback is used on second failure; template text matches config; fallback ticket is visible with notice; no infinite loop

**Merges into:** `develop`
**Depends on:** `feature/p3-llm-judge`
**Validation:** `make test` (retry/fallback tests pass).

---

#### `feature/p3-owner-edit-validation`

**What it contains:**
- `POST /api/v1/tickets/{ticket_id}/draft/validate` — runs deterministic validator on the submitted `reply_text`, returns `ValidationResult` (non-blocking, informational only)
- `EditOverride` model: `{reason: string, overridden_rules: list[str], actor: string, timestamp: ISO-8601}`
- If the owner proceeds despite warnings, `EditOverride` is appended to the audit log
- Edit diff (original draft vs. final sent text) stored in the audit log on approval
- Unit tests: validation returns correct warnings on flawed edited text; good edited text returns empty failure list; override reason is stored in audit log; override without reason is rejected

**Merges into:** `develop`
**Depends on:** `feature/p3-grounding-validator`
**Validation:** `make test` (owner-edit validation tests pass).

---

### Phase 4 Branches — Frontend and Metrics

These branches are largely independent of each other after `p4-api-types` is merged; they can be developed in parallel.

---

#### `feature/p4-api-types`

**What it contains:**
- `openapi-typescript` configured to generate TypeScript types from the FastAPI OpenAPI schema
- Generated types committed to `apps/web/src/api/types.ts` (or generated at build time)
- `make generate-types` Makefile target
- TanStack Query client setup in `apps/web/src/`
- All fetch hooks use the generated types; no hand-written response types anywhere
- Base URL, auth cookie handling, CSRF header injection set up as reusable utilities

**Merges into:** `develop`
**Depends on:** `feature/p3-retry-and-fallback` (requires the OpenAPI schema to be stable)
**Validation:** `npm run build` (TypeScript strict mode, zero type errors).

---

#### `feature/p4-ticket-queue`

**What it contains:**
- `GET /api/v1/tickets` — paginated list sorted by urgency (critical → high → normal → low), with filter by status/intent
- `apps/web/src/pages/QueuePage.tsx` — ticket queue view
- Intent badge, urgency badge (color-coded), escalation reason, age (e.g., "2h ago"), channel
- Visual distinction for escalated tickets (red border/badge) and suspected-injection tickets (warning icon)
- Status tabs: New / In Progress / Escalated / Approved / Rejected
- States: loading skeleton, empty state with icon, error state with retry, "server waking up" (after 8 s without response — spinner + message)
- TanStack Query: `useQuery` with polling every 30 s; retries on transient errors; no retries on mutations without idempotency keys
- Keyboard shortcut: `J/K` to navigate tickets in the list

**Merges into:** `develop`
**Depends on:** `feature/p4-api-types`
**Validation:** `make test` (frontend vitest); manual review against mock-store edge cases.

---

#### `feature/p4-approval-screen`

**What it contains:**
- `GET /api/v1/tickets/{ticket_id}` — full ticket detail with draft, citations, order snapshot, grounding result
- `POST /api/v1/tickets/{ticket_id}/approve` — records approval, triggers mock send
- `POST /api/v1/tickets/{ticket_id}/reject` — records rejection with required reason
- `apps/web/src/pages/ApprovalPage.tsx` — three-column layout:
  - **Left:** original customer message (always visible; raw, escaped)
  - **Center:** draft reply (editable inline)
  - **Right:** evidence panel — cited order fields (with values), cited policy passages (full text), classification flags, grounding result (rules G-1 through G-10 as pass/fail badges), judge confidence score
- Escalated / suspected-injection tickets: orange/red header banner; "View original message" required before approve button is enabled
- Suggestion-only drafts (ESC-8.3): yellow banner "Escalated — Suggestion Only"; secondary confirmation dialog on approve
- Inline editor: saves via `PATCH /api/v1/tickets/{ticket_id}/draft`; triggers client-side grounding warning call; if warnings exist, shows them before allowing save; submits `EditOverride.reason` if owner proceeds
- Reject: opens dialog requiring a reason (min 10 chars); reason stored in audit log
- Keyboard shortcuts: `A` = approve, `R` = reject, `E` = edit, focus-trapped in dialogs
- Accessibility: semantic `<article>`, `<aside>`, `<dialog>`; ARIA labels; keyboard-operable approve/edit/reject; sufficient contrast (WCAG AA); focus management
- No `dangerouslySetInnerHTML` anywhere

**Merges into:** `develop`
**Depends on:** `feature/p4-ticket-queue`, `feature/p3-owner-edit-validation`
**Validation:** `make test` (frontend vitest); manual review of all 31 edge cases from the owner seat.

---

#### `feature/p4-metrics-dashboard`

**What it contains:**
- `GET /api/v1/metrics?from=ISO&to=ISO` — computed from stored data, not in-memory counters
- Metrics returned: tickets by intent + urgency (breakdown), draft acceptance rate, `time_to_draft` (p50, p95), `time_to_send` (p50, business-hours-aware), grounding failures by rule (G-1 through G-10), escalation rate by reason, cost per ticket (tokens + INR), fallback template rate, policy IDs cited
- `apps/web/src/pages/DashboardPage.tsx`:
  - Time range picker: Last 7 days / Last 30 days / Custom date range
  - Grounding failures: horizontal bar chart by rule
  - Intent + urgency: stacked bar chart
  - Cost per ticket: line chart over time
  - `time_to_draft` and `time_to_send`: box plot or p50/p95 stat cards
  - Summary stat cards at the top (total tickets, acceptance rate, escalation rate)
  - Export as CSV button (`GET /api/v1/metrics/export.csv`)
- All charts use a small charting library (e.g., Recharts); no external analytics SDKs
- Loading skeletons for each chart section

**Merges into:** `develop`
**Depends on:** `feature/p4-api-types`
**Validation:** `make test` (frontend vitest); verify metrics match audit log counts on known seed data.

---

#### `feature/p4-audit-log-view`

**What it contains:**
- `GET /api/v1/tickets/{ticket_id}/audit` — paginated, ordered by timestamp
- Event types surfaced in UI: ingested, classified, pre-escalated, post-escalated, draft-created (with prompt hash and model), grounding-passed / grounding-failed (rule breakdown), grounding-retried, fallback-used, judge-result, approved (actor), rejected (actor + reason), edit (diff), edit-override (reason + overridden rules), mock-sent, retention-deleted
- `apps/web/src/components/AuditTimeline.tsx` — vertical timeline component, collapsible event details
- Ticket search by `ticket_id` or `external_message_id`
- No PII in any displayed log line (email is hashed/truncated; message body is not shown here)

**Merges into:** `develop`
**Depends on:** `feature/p4-api-types`
**Validation:** `make test`; verify timeline matches events for each of the 31 edge case tickets.

---

### Phase 5 Branches — Production Hardening

---

#### `feature/p5-full-eval`

**What it contains:**
- `make eval` target wired to run the full eval with live LLM
- Results committed to `evals/results/YYYY-MM-DD-<run-id>/report.json` and `report.md`
- Report includes: per-intent precision/recall + Wilson CI, escalation recall + Wilson CI, grounding failure rate, draft acceptance rate, `time_to_draft` p50/p95, `time_to_send` p50, cost per ticket, bad-draft corpus catch rate, false-block rate on good drafts, policy-ID coverage matrix
- `evals/datasets/bad_drafts.jsonl` grown to 100+ violations
- Report format documented in `evals/README.md`

**Merges into:** `develop`
**Depends on:** all Phase 3 and Phase 4 branches merged
**Validation:** `make eval` completes and produces a report; escalation recall ≥ 0.95; grounding failure rate near 0%.

---

#### `feature/p5-resilience`

**What it contains:**
- `make resilience` target — automated shell script that:
  1. Starts the stack; asserts `/readyz` returns 200
  2. Stops the DB; asserts `/readyz` returns 503 and `/healthz` returns 200
  3. Starts the DB; asserts `/readyz` recovers to 200 without API restart
  4. Submits a ticket; kills the API mid-processing; restarts; asserts sweeper re-queues; asserts no duplicate draft; asserts ticket reaches terminal state
- API startup: exponential backoff on DB connect (capped at 60 s); degraded mode until connected
- SIGTERM handler: stop accepting work, drain jobs, close pool
- Tests for the sweeper: stale job is re-queued; non-stale job is not re-queued; duplicate is not created

**Merges into:** `develop`
**Depends on:** `feature/p2-job-runner`
**Validation:** `make resilience` passes all 4 steps.

---

#### `feature/p5-data-retention`

**What it contains:**
- Scheduled job (runs once daily via a separate worker loop or cron within the job table): soft-deletes (marks `deleted_at`) ticket data older than `settings.retention_days` (default 90 days)
- Deletion is recorded in the audit log
- Customer erasure endpoint: `DELETE /api/v1/customers/{email}` — marks all tickets for that email as deleted and removes PII fields; requires owner auth
- Unit tests: retention job deletes tickets older than threshold; does not delete recent tickets; erasure marks all tickets for email

**Merges into:** `develop`
**Depends on:** `feature/p2-intake`
**Validation:** `make test` (retention tests pass).

---

#### `feature/p5-ci-cd-finalize`

**What it contains:**
- `ci.yml` finalized: all jobs wired up and passing on branch `develop`
- `.github/workflows/staging.yml`: triggers on merge to `develop`; deploys to staging; runs smoke eval; notifies on failure
- `.github/workflows/release.yml`: triggers on `v*` tag; builds production images; deploys to production; runs synthetic check (POST test ticket, expect terminal state within 30 s); notifies
- `docker-compose.staging.yml` and `docker-compose.prod.yml` — production-grade compose files (non-root, multi-stage, pinned base tags)
- Synthetic check script: `scripts/synthetic_check.py`
- External uptime monitor setup documented in `docs/runbook.md`

**Merges into:** `develop`, then `develop` → `main` as the final release PR
**Depends on:** `feature/p5-full-eval`, `feature/p5-resilience`, `feature/p5-data-retention`
**Validation:** `make lint && make test && make eval && make resilience` all pass on `develop`; deploy to staging succeeds; `GET /readyz` green on staging.

---

#### `feature/p5-documentation`

**What it contains:**
- `README.md` — final: what it is, architecture diagram, `docker compose up` instructions, eval results table (date, model, prompt hashes), demo GIF or video, known limitations, deployment notes, which Git tag the demo runs
- `docs/runbook.md` — what can go down, how it shows up in logs, how to check, how to recover, uptime monitor setup, free-tier limits (if any), SIGTERM behavior
- `docs/adr/0001-concurrency.md` finalized
- `docs/adr/0002-job-runner.md` finalized
- `docs/adr/0003-llm-provider.md` finalized with chosen models
- Known limitations: DPDP Act, no real store integration, no multi-language, no auto-send

**Merges into:** `develop`
**Depends on:** `feature/p5-full-eval` (needs eval results to write the table)
**Validation:** README renders correctly; all ADRs present; runbook covers all components.

---

### Full Branch Dependency Graph

```
p1-repo-scaffold
  └── p1-data-contract
        └── p1-db-schema
              ├── p1-mock-store
              │     └── p1-eval-harness
              │           └── p1-keyword-baseline
              │                 └── p2-pre-lookup-escalation ←┐
              ├── p2-health-endpoints                          │
              ├── p2-owner-auth                                │
              ├── p2-job-runner                                │
              │     └── p2-intake ──────────────────────────┐ │
              └── p2-llm-client                              │ │
                    └── p2-classification ──────────────────┼─┘
                          └── p2-context-lookup             │
                                └── p2-post-lookup-escalation
                                      └── p2-policy-retrieval
                                            └── p3-drafting
                                                  └── p3-grounding-validator
                                                        ├── p3-llm-judge
                                                        │     └── p3-retry-and-fallback
                                                        │           └── p4-api-types
                                                        │                 ├── p4-ticket-queue
                                                        │                 ├── p4-metrics-dashboard
                                                        │                 └── p4-audit-log-view
                                                        └── p3-owner-edit-validation
                                                              └── p4-approval-screen
                                                  
All Phase 4 merged → p5-full-eval
p2-job-runner merged → p5-resilience  
p2-intake merged → p5-data-retention
All Phase 5 features → p5-ci-cd-finalize → p5-documentation
```

---

## Part 2 — Environment Setup

You have four environments. Each is entirely separate: separate databases, separate secrets, separate deployed builds.

```
Local (dev)  →  CI  →  UAT (staging)  →  Non-Prod  →  Prod
```

> In your case, the four environments map as follows:
> - **Dev** = your local machine (`docker compose up`)
> - **UAT** = staging on Render (or equivalent), deployed automatically on merge to `develop`
> - **Non-prod** = a separate Render service used for release candidate testing before tagging
> - **Prod** = production on Render, deployed only on a `v*` Git tag

---

### Environment 1 — Dev (Local)

**Purpose:** fast iteration; full stack runs locally from a single command.

#### Setup

**Step 1: Install prerequisites**
```bash
# Windows (PowerShell as admin)
winget install Docker.DockerDesktop
winget install astral-sh.uv
winget install OpenJS.NodeJS.LTS
winget install Git.Git
```

**Step 2: Clone and configure**
```bash
git clone <your-repo-url>
cd second_e2e_app
cp .env.example .env
# Edit .env — fill in:
#   LLM_API_KEY, LLM_MODEL, JUDGE_MODEL
#   OWNER_EMAIL, OWNER_PASSWORD_HASH (run: python -c "import argon2; print(argon2.PasswordHasher().hash('yourpassword'))")
#   HMAC_SECRET (any random string for local dev)
#   BASE_URL=http://localhost:8000
```

**Step 3: Start the stack**
```bash
make up          # docker compose up -d (db, api, mock-store, web)
make migrate     # run Alembic migrations
make seed        # seed the mock store with 31 edge cases
```

**Step 4: Verify**
```bash
curl http://localhost:8000/healthz   # → {"status":"ok"}
curl http://localhost:8000/readyz    # → {"status":"ok"}
# Open http://localhost:3000 in your browser
```

**Step 5: Run tests locally**
```bash
make lint        # ruff + mypy + eslint + prettier
make test        # unit + pipeline tests (no live LLM)
make eval-smoke  # smoke eval with recorded fixtures
```

#### `.env` variables used in Dev

```bash
# Database
DATABASE_URL=postgresql+asyncpg://replydesk:replydesk@localhost:5432/replydesk

# API
SECRET_KEY=dev-secret-key-change-in-prod
BASE_URL=http://localhost:8000
CORS_ALLOWED_ORIGINS=http://localhost:3000

# LLM
LLM_PROVIDER=openai          # or anthropic, google, etc.
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o
JUDGE_MODEL=claude-3-5-sonnet-20241022
LLM_TIMEOUT_SECONDS=30

# Auth
OWNER_EMAIL=owner@example.com
OWNER_PASSWORD_HASH=<argon2 hash>
HMAC_SECRET=dev-hmac-secret

# Store profile
STORE_NAME=Kaveri Home Goods
STORE_SIGN_OFF=Kaveri Home Goods Support
STORE_TIMEZONE=Asia/Kolkata

# Mock store
MOCK_STORE_URL=http://mock-store:8001
FROZEN_NOW=2026-10-15T12:00:00Z   # controls seed dates in dev/test

# Jobs
LOCK_TIMEOUT_SECONDS=60
SWEEP_INTERVAL_SECONDS=30

# Retention
RETENTION_DAYS=90

# Policy
POLICY_FILE_PATH=docs/policies.md
POLICY_VERSION=1.1
```

---

### Environment 2 — CI (GitHub Actions)

**Purpose:** automated validation on every PR. No live LLM calls; no live external services.

#### How it works

CI is fully automated — you don't run it manually. Every push to any branch triggers it.

**What CI uses instead of real services:**
- `FakeLLMClient` — returns recorded outputs from `evals/datasets/recorded_outputs/`
- GitHub Actions service container for Postgres + pgvector (spun up per job)
- `FROZEN_NOW` env var set to a fixed timestamp so seed dates are stable

#### Setting up CI secrets (one-time, in GitHub repo settings)

1. Go to **GitHub repo → Settings → Secrets and variables → Actions**
2. Add these repository secrets:

| Secret name | Value |
|---|---|
| `CI_DATABASE_URL` | Provided by the GitHub Actions Postgres service (set in workflow YAML) |
| `CI_SECRET_KEY` | Any random string (used for JWT signing in CI) |
| `CI_HMAC_SECRET` | Any random string |

3. These are **not** the production secrets — CI secrets are isolated.

#### CI environment variables (set in `ci.yml`, not secret)

```yaml
env:
  DATABASE_URL: postgresql+asyncpg://ci:ci@localhost:5432/ci_replydesk
  BASE_URL: http://localhost:8000
  CORS_ALLOWED_ORIGINS: http://localhost:3000
  LLM_PROVIDER: fake        # uses FakeLLMClient; no real API key needed
  MOCK_STORE_URL: http://localhost:8001
  FROZEN_NOW: "2026-10-15T12:00:00Z"
  STORE_SIGN_OFF: "Kaveri Home Goods Support"
  POLICY_FILE_PATH: docs/policies.md
  POLICY_VERSION: "1.1"
  RETENTION_DAYS: "90"
```

---

### Environment 3 — UAT / Staging

**Purpose:** integration testing by the owner before release; runs on real services with limited seed data; smoke eval runs against it. Deployed automatically on every merge to `develop`.

#### One-time setup on Render (repeat for non-prod if needed)

**Step 1: Create a Render account and project**  
Go to [render.com](https://render.com) and create a new account/project.

**Step 2: Create a managed Postgres database**
- Dashboard → New → PostgreSQL
- Name: `replydesk-staging-db`
- Plan: Starter (for demo) or Standard (for UAT)
- Enable `pgvector` extension: in the Render Postgres console, run:
  ```sql
  CREATE EXTENSION IF NOT EXISTS vector;
  ```
- Copy the **internal connection URL** (used by the API service)

**Step 3: Create a container registry (if not using Render's native build)**  
Use GitHub Container Registry (`ghcr.io`):
- Go to GitHub → Settings → Developer settings → Personal access tokens → Create one with `write:packages` scope
- Add it as a GitHub secret: `GHCR_PAT`

**Step 4: Create Render Web Services for each component**

For each service (`api`, `mock-store`), create a Render Web Service:
- Source: Docker image from `ghcr.io/<your-org>/replydesk-api:main-<sha>`
- Set all environment variables (see below)
- Health check path: `/healthz` (for API)

For the frontend: create a Render Static Site from the built `apps/web/dist/`

**Step 5: Set environment variables in Render (staging)**

In Render dashboard → your service → Environment → Add:

```bash
DATABASE_URL=<internal postgres connection string from step 2>
SECRET_KEY=<random 32+ char string, generated once>
BASE_URL=https://replydesk-api-staging.onrender.com
CORS_ALLOWED_ORIGINS=https://replydesk-staging.onrender.com
LLM_PROVIDER=openai
LLM_API_KEY=<your real key>
LLM_MODEL=gpt-4o
JUDGE_MODEL=claude-3-5-sonnet-20241022
LLM_TIMEOUT_SECONDS=30
OWNER_EMAIL=owner@example.com
OWNER_PASSWORD_HASH=<argon2 hash>
HMAC_SECRET=<random string>
MOCK_STORE_URL=https://replydesk-mock-staging.onrender.com
STORE_NAME=Kaveri Home Goods
STORE_SIGN_OFF=Kaveri Home Goods Support
STORE_TIMEZONE=Asia/Kolkata
POLICY_FILE_PATH=docs/policies.md
POLICY_VERSION=1.1
FROZEN_NOW=              # leave blank in staging — use real time
LOCK_TIMEOUT_SECONDS=60
SWEEP_INTERVAL_SECONDS=30
RETENTION_DAYS=90
ENVIRONMENT=staging
```

**Step 6: Add the staging deploy hook to `staging.yml`**  
In Render: service → Settings → Deploy hook → copy the URL  
Add as GitHub secret: `RENDER_STAGING_DEPLOY_HOOK`

**Step 7: Run migrations on first deploy**
```bash
# SSH into the Render service (or use the Render shell)
alembic upgrade head
# Then seed the mock store
python services/mock-store/seed/seed.py
```

#### How staging deploys automatically

Every merge to `develop` triggers `.github/workflows/staging.yml`:
1. CI jobs run
2. Docker images built and pushed tagged `main-<sha>`
3. Render deploy hook called → Render pulls new image and restarts
4. Smoke eval runs against `$STAGING_API_URL`
5. Slack/email notification sent

#### Verify staging

```bash
curl https://replydesk-api-staging.onrender.com/healthz
curl https://replydesk-api-staging.onrender.com/readyz
# Open https://replydesk-staging.onrender.com in browser
```

---

### Environment 4 — Non-Prod (Release Candidate)

**Purpose:** a stable environment that mirrors production exactly; used to validate a release candidate before it is tagged and pushed to production. Not auto-deployed — requires manual trigger or a specific branch pattern (e.g., `release/v*`).

#### Setup

Non-prod is a **second set of Render services**, identical to staging but with a separate database and separate secrets. Follow the same Render setup steps above with different names:

- Service names: `replydesk-api-nonprod`, `replydesk-nonprod-db`, `replydesk-mock-nonprod`
- Use the same environment variables as staging, but with different values for `DATABASE_URL`, `BASE_URL`, `CORS_ALLOWED_ORIGINS`, `HMAC_SECRET`, `SECRET_KEY`
- Add `ENVIRONMENT=nonprod`

#### Triggering non-prod deploys

Create a manual workflow or trigger on `release/*` branches:

```yaml
# .github/workflows/nonprod.yml
on:
  push:
    branches: ['release/*']
  workflow_dispatch:  # manual trigger
```

#### Release candidate process

1. Cut a release branch from `develop`: `git checkout -b release/v1.0.0`
2. Merge to `release/v1.0.0` triggers non-prod deploy
3. Owner runs `make eval` against non-prod API URL
4. Owner manually reviews 5 tickets per intent
5. If green: merge `release/v1.0.0` → `main`; tag: `git tag v1.0.0`

---

### Environment 5 — Production

**Purpose:** the live demo; always runs the latest tagged release.

#### Setup

Follow the same Render steps as staging, with separate services and a production-grade database plan:

- Service names: `replydesk-api-prod`, `replydesk-prod-db`, `replydesk-mock-prod`
- Use a paid Render plan (not the free tier) for the demo path — free tier sleeps after inactivity
- Add `ENVIRONMENT=production`
- Set all environment variables as in staging but with production values

**Production-only additions:**
- External uptime monitor: go to [betteruptime.com](https://betteruptime.com) or [uptimerobot.com](https://uptimerobot.com)
  - Monitor URL: `https://replydesk-api.onrender.com/readyz`
  - Check every 1 minute
  - Alert by email on failure
  - Document the monitor setup in `docs/runbook.md`

#### Production deploy process (tag-based)

```bash
# On the main branch after non-prod validation
git checkout main
git merge release/v1.0.0
git tag v1.0.0
git push origin main --tags
```

GitHub Actions `release.yml` runs automatically:
1. Verifies CI is green on the tagged commit
2. Builds production images tagged `v1.0.0`
3. Pushes to `ghcr.io`
4. Calls Render production deploy hook (`RENDER_PROD_DEPLOY_HOOK`)
5. Runs synthetic check: POSTs a seeded test ticket; expects a draft/escalation within 30 s
6. Notifies on success or failure

#### GitHub secrets needed for production deploy

Add these to your GitHub repo secrets:

| Secret | Description |
|---|---|
| `GHCR_PAT` | GitHub token with `write:packages` |
| `RENDER_STAGING_DEPLOY_HOOK` | Render deploy hook URL for staging |
| `RENDER_NONPROD_DEPLOY_HOOK` | Render deploy hook URL for non-prod |
| `RENDER_PROD_DEPLOY_HOOK` | Render deploy hook URL for production |
| `SYNTHETIC_CHECK_API_URL` | Production API base URL for the synthetic check |
| `SYNTHETIC_CHECK_HMAC_SECRET` | HMAC secret for the synthetic check's test ticket |
| `NOTIFY_EMAIL` | Email address for CI/deployment notifications |

---

### Environment Comparison Summary

| Aspect | Dev | CI | UAT / Staging | Non-Prod | Prod |
|---|---|---|---|---|---|
| Trigger | Manual (`make up`) | Every PR push | Merge to `develop` | Push to `release/*` or manual | `v*` tag push |
| Database | Local Docker Postgres | GitHub Actions ephemeral Postgres | Render managed (separate) | Render managed (separate) | Render managed (separate) |
| LLM calls | Real (your key) | Fake (`FakeLLMClient`) | Real | Real | Real |
| Mock store | Local Docker | Local Docker | Render service | Render service | Render service |
| Seed data | 31 edge cases (`make seed`) | 31 edge cases (seeded in CI) | Limited seed | Full seed | No auto-seed |
| Migrations | `make migrate` (manual) | `alembic upgrade head` (automated) | Automated on deploy | Automated on deploy | Automated on deploy |
| Uptime monitor | None | None | None | None | External monitor on `/readyz` |
| `FROZEN_NOW` | Set (for test reproducibility) | Set | Not set (real time) | Not set | Not set |
| `ENVIRONMENT` var | `local` | `ci` | `staging` | `nonprod` | `production` |

---

### Quick Reference: Promotion Path

```
feature/* ──PR──► develop ──auto──► staging (UAT)
                    │
                 manual cut
                    ▼
              release/v1.0.0 ──auto──► non-prod
                    │
              manual review OK
                    │
                    ▼
                merge to main
                tag v1.0.0  ──auto──► production
```
