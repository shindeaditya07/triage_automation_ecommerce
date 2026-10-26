# ReplyDesk: Engineering Rules for Coding Agents

Audience: any coding agent (or human) writing code in this repository.
Status: binding. These rules apply to every change, however small.
Version: 1.1 (2026-10-15), aligned with `spec-amendments.md` (A1-A19, pending owner approval)

---

## 0. Precedence and how to handle conflicts

Order of authority when documents disagree:

1. `spec.md` (source of truth for behavior; any request that contradicts it must be flagged, not silently obeyed)
2. `policies.md` (source of truth for store policy facts and policy IDs)
3. This file (how to build it)
4. `docs/build-plan.md` (schedule and rationale)

Rules:

- If a task, a spec section, and a policy conflict, **stop and flag it** in your reply with the section numbers. Do not pick a side silently.
- Do not edit `spec.md` or `policies.md` unless the task explicitly says to. Propose the edit instead.
- **Pending spec amendments (A1-A19).** Listed in `docs/spec-amendments.md` and *not yet approved*. Until `spec.md` is bumped to a version that includes them, do not implement them silently; build the code so they are easy to add, and mention them when relevant. The ones that most affect code structure:
  - A1: intent enum gains `return`, `cancellation`, `address_change`, `payment`, `warranty`.
  - A2/A3: two-stage escalation (before and after order lookup) with extra rules; `high_value` is set by the rules engine, not the classifier.
  - A4/A5: grounding rule 1 split (order values vs policy values); drafts state policy and never promise outcomes (TON-9.2).
  - A6/A7: an order data contract and ticket attachments metadata.
  - A9: time semantics (IST windows, business days) pending the policy author's decision.
- **Blockers.** A5, A6, A7, A9 and A10 block the code that depends on them. Build around them with clearly marked placeholders and ask the owner; do not guess.
- If the answer to a question depends on a fact you do not have (a policy number, a price, a model name), **look it up or ask**. Never guess a fact and never hard-code an invented one.

---

## 1. Safety invariants (never violate, never weaken)

These are the product. A change that breaks one is a defect even if every test passes.

1. **Owner approval gate.** Nothing is sent to a customer without an explicit owner approval event stored in the audit log. There is no code path from drafting to sending that bypasses it.
2. **Fail closed.** If a check cannot run or errors (validator, judge, retrieval, lookup), the ticket is escalated to the owner. An error is never treated as a pass.
3. **Grounding gate.** A draft reaches the approval queue only after the deterministic validator passes, and then the LLM judge passes. After two failures, use the fallback template and flag the ticket.
4. **Drafting has no tools.** The drafting call receives data and returns JSON. It cannot trigger refunds, lookups, or side effects. `proposed_action` is a suggestion that the system never executes.
5. **Customer text is untrusted data.** It is only ever passed to an LLM inside clearly delimited data blocks, never concatenated into instructions. Instructions found inside a message are not obeyed.
6. **Privacy isolation.** Data from one customer's order never appears in another customer's ticket context, draft, log line, or error message. Order lookup is scoped by the verified match between sender and order.
7. **No real money, no real stores.** Refunds, payments, and real e-commerce integrations are out of scope. The mock store is the only store.
8. **Policy facts have one source.** Policy numbers (return window, damage window, shipping threshold, escalation threshold, COD limit, refund timelines, warranty) live only in `policy_constants.yaml`, each keyed by its policy ID. Application logic reads that file; no literals elsewhere. A CI test asserts every value appears in its cited passage of `policies.md`, and a lock file holds the SHA-256 of `policies.md` so CI fails if the policy changed without a version bump (the policy file requires a bump and an eval rerun).
9. **State policy, never promise outcomes (TON-9.2, ESC-8.2, ESC-8.3).** A draft may say what policy provides, with a citation, and that the owner will confirm the result. It must not commit the store to a refund, replacement, compensation, or date. Escalated tickets never receive a resolution promise.

---

## 2. Repository layout and tooling

```
apps/web/            React + TypeScript frontend
services/api/        FastAPI backend (the product)
services/mock-store/ FastAPI mock store with seeded data
evals/               datasets, runner, results, baseline
docs/                build-plan, runbook, ADRs
docker-compose.yml   db, api, mock-store, web
Makefile             single entry point for dev tasks
```

- Python 3.12. Environments via `uv` or `venv`. **Never Conda.**
- Dependencies are pinned (lockfile committed): `uv.lock`, `package-lock.json`.
- Every task has a Makefile target: `make up`, `make test`, `make lint`, `make eval`, `make eval-smoke`, `make resilience`, `make migrate`.
- One `docker compose up` must start the full stack from a clean clone plus `.env` copied from `.env.example`.
- No dead code, no duplicate implementations (the legacy portfolio repo had a JS server beside a TS server; do not repeat that). Delete rather than comment out.

---

## 3. Backend coding standards (Python)

- Full type hints. `mypy` clean on `services/api` and `evals`. `ruff` (lint + format) clean.
- Pydantic v2 models at every boundary: request bodies, responses, LLM outputs, config. The same models generate the JSON Schema used for LLM structured output, so the schema and the validator cannot drift.
- Configuration via a single typed settings object loaded from environment variables. No `os.environ` reads scattered through the code. Fail at startup with a clear message if required settings are missing.
- No raw SQL string building. Use SQLAlchemy 2.0 constructs with bound parameters.
- Keep the pipeline stages pure where possible: input in, output out, no hidden global state. Side effects (DB writes, HTTP calls) live at the edges and behind interfaces so they can be faked in tests.
- One sync/async style, chosen once and recorded in `docs/adr/0001-concurrency.md`. Do not mix blocking calls into async code.
- Time: store timestamps as timezone-aware UTC. **Evaluate policy windows in `Asia/Kolkata`**, because RET-1.1 counts calendar days from the delivery date while DMG-3.1 counts 48 hours from the delivery timestamp. Business days exclude a configured holiday list. Exact boundary rules await amendment A9; implement them in one module so one change fixes everything, with boundary tests (exactly 7 days, exactly 48 h, one second over). Never read "now" inside business logic; pass the clock in so tests can freeze it.
- Money: integers in paise or `Decimal`, never floats. Format only at the edge.
- Errors: raise typed domain exceptions; map them to HTTP responses in one place. Never leak stack traces or internal messages to clients in production.
- Input limits: maximum body size, maximum message length, maximum attachments count; reject early with 413/422.

---

## 4. Resilience rules (lessons from the portfolio outage)

The portfolio's database connection dropped after idle time and nothing alerted the owner. These rules exist so that class of incident cannot recur.

**4.1 Connections**
```python
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,   # test connection before use; replace dead ones
    pool_recycle=1800,    # retire connections before servers/networks kill them
    pool_timeout=10,      # fail fast instead of hanging requests
    pool_size=5,
    max_overflow=5,
)
```
Every outbound call (DB, LLM, mock store) has an explicit timeout. No call waits indefinitely.

**4.2 Startup and shutdown**
- The API must start and listen even when the database is down. Connect with exponential backoff (capped), report degraded status until connected, and exit non-zero only after sustained failure so the host restarts it.
- Handle SIGTERM: stop accepting work, finish or re-queue in-flight tickets, close pools.
- Migrations run as a separate step (`make migrate`, or a release/init job), never inside API startup.

**4.3 Health endpoints (two, different jobs)**
- `/healthz`: liveness. Process is up. **Never touches the database.** This is what the host's restart logic watches.
- `/readyz`: readiness. Checks the database (`SELECT 1`), pgvector extension present, migrations at head, and that the policy index is loaded. Returns 503 with a JSON body naming the failing component. This is what the uptime monitor watches.

**4.4 Background work must survive restarts**
FastAPI `BackgroundTasks` die when the instance restarts or sleeps. Therefore use a **DB-backed job table** claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, run by a worker loop in the API process. Record this in an ADR; it is a deliberate step beyond the spec's "simple background tasks first".
- Ticket `status` follows the spec's enum (plus `failed`, amendment A7). "Processing" lives in the job table (`state`, `locked_at`, `attempts`), not in `status`.
- A startup sweeper and a periodic sweeper re-queue jobs locked longer than a timeout.
- Every stage is idempotent (ingestion on `external_message_id`; draft generation keyed by ticket + input hash) so a retry never creates duplicates.
- A ticket that cannot be processed ends in a visible `failed` or `escalated` state with a reason, never a silent stall.

**4.5 LLM and external dependency failures**
- Timeouts, bounded retries with backoff and jitter on transient errors only, and a circuit breaker so a provider outage degrades to "escalate to owner" quickly.
- Provider errors never produce a draft. They produce an escalation with the error class recorded.

**4.6 Frontend cold-start tolerance**
- TanStack Query configured with retries and backoff for idempotent reads; no retries on mutations without idempotency keys.
- Visible states for loading, empty, error, and "server waking up" (after N seconds without a response). No blank screens, no silent failures.

**4.7 Detection**
- An external uptime monitor on `/readyz` with email alerts. Document its setup in `docs/runbook.md`.
- A scheduled synthetic check (post a seeded test ticket, expect a draft or an escalation within the latency budget).
- If hosting is on a free tier, document exactly what sleeps, for how long, and how to wake it. Do not depend on free tiers for the demo path.

---

## 5. LLM layer rules

- All model calls go through one `LLMClient` interface (`classify`, `draft`, `judge`, `embed`) with provider adapters. Application code never imports a provider SDK directly.
- Structured output only. Parse and validate with Pydantic; invalid output is a failure, retried once with the validation error, then escalated.
- Prompts live in versioned files under `services/api/prompts/`. Hash each prompt file; record `prompt_version`, `prompt_hash`, `model`, `temperature`, token counts, latency, and cost in INR for every call. Prices live in config, not code.
- `temperature = 0` for classification and judging. Record the value used.
- The judge model must be from a different model family than the drafter where possible. Never let a model grade its own output unchecked.
- Prompt structure: system instructions are static. Customer content and retrieved facts go in delimited data blocks (for example `<customer_message>...</customer_message>`) with an explicit statement that block content is data. Neutralize delimiter strings found inside customer text.
- Send the minimum personal data needed. Do not send customer email addresses or unrelated order fields to the model when a ticket-local identifier will do.
- Every draft must be reproducible: store the inputs (order snapshot, retrieved passage IDs, policy version, prompt hash, model) alongside the output.

---

## 6. Grounding validator rules

- The deterministic validator runs first and is the authority for numbers, dates, IDs, and tracking numbers. The LLM judge only adds semantic coverage; it can block but never override a deterministic failure.
- Extract every amount, date, order number, and tracking number from `reply_text` and compare it, exactly and normalized (currency symbols, separators, date formats), against cited fields. An extracted value with no matching citation fails the draft.
- Outcome promises ("we will refund", "we'll replace", "you will receive by ...", compensation, discount codes) are detected with a maintained phrase list and **always fail** (TON-9.2). Policy statements ("our policy provides a refund within 5-7 business days once approved") are allowed only with a cited policy ID whose conditions the order meets, checked by code against the order data and `policy_constants.yaml`.
- Every cited policy ID must exist in the loaded policy set and every cited order field must exist in the order snapshot; a hallucinated citation fails the draft.
- Values from policy text must match the cited passage; values from the order must match the cited order field (amendment A4).
- Tone checks (TON-9.1, TON-9.3): exact sign-off from the store profile, at most one apology phrase, length limit, customer name used only when known.
- Detect: other-customer data (IDs or emails not belonging to this ticket), echoing of injected instructions, promises outside policy.
- The validator also runs on owner-edited text as a non-blocking warning; overriding it requires a recorded reason (amendment A12).
- Failure output is structured: which rule failed, which span, which citation was missing. This feeds the retry prompt, the audit log, and the dashboard's "caught by rule" metric.
- Treat the validator as the most heavily tested module: it needs both positive and adversarial fixtures (see section 10).

---

## 7. Security rules

- **Auth:** single owner account. Passwords hashed with bcrypt or argon2. Session via a JWT in an httpOnly, Secure, SameSite cookie, never in `localStorage`. Short expiry, refresh strategy documented. CSRF protection for cookie-authenticated mutations.
- **Brute force:** rate limit login and ingestion endpoints; lock out or back off on repeated failures.
- **Ingestion (amendment A14):** the public form endpoint needs rate limits, size limits, and a honeypot or CAPTCHA. The email-style JSON endpoint needs HMAC or API-key authentication. Replies go only to the address on the ticket.
- **Sensitive data in messages (amendment A13):** customers send bank, UPI, and possibly card details (REF-2.3). Mask card, bank-account, UPI, and government-ID-like numbers before storage and before any LLM call. Never store card numbers.
- **Order lookup scope:** the lookup takes `(order_id, sender_email)`. On mismatch it returns a flag only; no other-customer order data enters the LLM context or the draft (TON-9.4).
- **CORS:** exact origin allowlist from environment. No substring or wildcard matching.
- **Password reset or any emailed link:** build URLs from a configured base URL, never from request headers such as `Origin` or `Host`.
- **Secrets:** only in environment variables or a secrets manager. `.env` is gitignored; `.env.example` has placeholders only. Never log secrets. Run a secret scanner in CI.
- **Untrusted content:** inbound email and form content is sanitized (strip markup, links, hidden text) before processing. Output rendered in the UI is escaped; never use `dangerouslySetInnerHTML` on customer or model text.
- **Dependencies:** pinned; `pip-audit` and `npm audit` in CI with a documented policy for exceptions.
- **Headers:** set standard security headers on the web app (CSP, X-Content-Type-Options, frame protections).
- **Containers:** multi-stage builds, non-root user, pinned base image tags, no dev tools in runtime images.
- **Logging and privacy:** do not log message bodies or raw email addresses. Log ticket and request IDs; hash or truncate identifiers where needed. Redact before logging.

---

## 8. Data and persistence rules

- All schema changes go through Alembic migrations. No manual DDL. Migrations are reviewed, reversible where practical, and tested against an empty database in CI.
- Database constraints enforce invariants, not only application code: unique `external_message_id`, foreign keys, non-null where the spec requires, enum checks for status and intent.
- The `audit_log` is append-only. No UPDATE or DELETE paths for it in application code. Every ticket event, draft, edit, approval, rejection, and send is recorded with actor and timestamp.
- Order data used for a draft is snapshotted with the draft so the draft stays reproducible after the order changes.
- Retention: a scheduled job removes ticket data after the configured period (default 90 days for the demo) and records that it ran.
- Policy passages are re-embedded when the `policies.md` version changes; the index records which policy version it was built from. `/readyz` fails if they mismatch.
- Retrieval always includes deterministic passages by intent in addition to vector results, and retrieval recall is measured in evals.

---

## 9. Observability

- Structured JSON logs. Every line carries `request_id`; ticket-scoped lines also carry `ticket_id` and `stage`.
- Metrics required by spec FR-12 and the dashboard (acceptance rate, time to first response, grounding failures by rule, escalation rate, cost per ticket) are computed from stored data, not ad hoc counters that reset on restart.
- Each pipeline stage records duration; the end-to-end latency budget (spec target: 10 s to draft) is measured and reported.
- `docs/runbook.md` must answer: what can go down or sleep, how it shows up, how to check logs, how to recover, who gets alerted.

---

## 10. Testing and evaluation rules

- **Unit tests:** every pure function in the pipeline, especially escalation rules and the grounding validator. Target at least 95% branch coverage on `grounding/` and `escalation/`; do not chase coverage elsewhere at the expense of meaningful tests.
- **Fixtures for the 20 mock-store edge cases** (spec section 17) are shared by unit, pipeline, and eval tests. Dates are generated relative to a frozen clock so windows stay valid.
- **Adversarial tests:** prompt injection in the body, in the subject, and in hidden HTML; delimiter-breaking text; another customer's order ID; mixed intents; vague messages; oversized input; unicode tricks in amounts and dates.
- **Pipeline tests:** run the whole flow against the mock store with a fake `LLMClient` returning recorded outputs. CI makes no live LLM calls.
- **Evals** (`make eval`): labeled set of 60-100 tickets, per-intent precision and recall, keyword baseline, escalation recall, grounding failures, cost per ticket. Each run stores the model, prompt hashes, and results under `evals/results/`. Report all numbers, including bad ones.
- **Seeded bad drafts:** a corpus of drafts with known grounding violations; the validator must catch every one. Also measure false blocks on good drafts.
- **Resilience test** (`make resilience`):
  1. Start the stack; `/readyz` returns 200.
  2. `docker compose stop db`; `/readyz` returns 503 while `/healthz` stays 200.
  3. `docker compose start db`; `/readyz` recovers to 200 without restarting the API.
  4. Kill the API mid-processing; after restart, the sweeper re-queues the stuck ticket and no duplicate draft is created.
- **Never weaken a test to make it pass.** If a test is wrong, fix it and say why; if code is wrong, fix the code.
- Labels are written or reviewed by a human. Do not generate eval labels with the same model that is being evaluated.
- **Coverage matrix:** every policy ID is exercised by at least one labeled ticket; CI fails if a policy ID referenced by a label does not exist in `policies.md`.
- **Report uncertainty:** show Wilson confidence intervals next to precision, recall, and escalation recall. With 60-100 tickets a single miss moves these numbers a lot; say so in the README.
- **Escalation backstops:** deterministic keyword/regex rules for legal, chargeback, and abusive language run alongside the classifier flag (amendment A3).
- **Mock-store edge cases** extend beyond spec section 17 (amendment A16): missing photo, failed delivery, courier ETA, personalized/consumable/gift-card items, store-credit order, remote pin code, Sunday/holiday order, non-English and multi-intent messages. COD orders never exceed ₹5,000; the high-value case is prepaid.
- **Metrics:** report `time_to_draft` (system) and `time_to_send` (owner, business-hours aware) separately (amendment A18).

---

## 11. Frontend rules

- TypeScript `strict: true`. No `any` without a comment explaining why.
- API types are generated from the FastAPI OpenAPI schema (`openapi-typescript`); do not hand-write response types.
- Server state through TanStack Query. No ad hoc `useEffect` fetching.
- The approval screen shows, side by side: the original customer message, the draft, the evidence (cited order fields and policy passages), flags, and any grounding result. Editing records a diff in the audit log.
- Escalated and suspected-injection tickets are visually distinct and cannot be one-click approved without viewing the original message.
- States: loading, empty, error, and waking-up are implemented for every data view.
- Accessibility: semantic elements, keyboard operable approve/edit/reject, sufficient contrast, focus management in dialogs.
- Style: dense, utilitarian, consistent Tailwind tokens. Skip decorative animation; the product is judged on clarity and correctness.
- No tokens in `localStorage`. No secrets in the bundle.

---

## 12. CI/CD and deployment

- **Pre-commit:** ruff, ruff format, mypy (changed files), eslint, prettier, secret scan.
- **GitHub Actions on every PR:** lint, type check, unit and pipeline tests, frontend build and tests, migration check against an empty DB, dependency audit, eval smoke run on recorded fixtures (no live API), container build.
- Images build reproducibly, run as non-root, and read all config from environment variables (12-factor). No local file paths in code.
- Deployment target for the demo: a host that runs Docker services and managed Postgres with pgvector (for example Render), with the frontend static. AWS (ECS Fargate, RDS, S3 + CloudFront, Secrets Manager, Terraform) is a stretch goal; keep everything portable so that move is mechanical.
- Do not put the demo path on a tier that sleeps or expires. If a free tier is used anywhere, document the limit in the runbook.
- If the frontend and API are on different domains, configure cookies (`SameSite`, `Secure`) and CORS for that exact origin pair; prefer a single origin behind a reverse proxy.
- Releases are tagged; the README states which tag the demo runs.

---

## 13. Documentation rules

- README: what it is, architecture diagram, how to run (`docker compose up`), eval results table with date and model, short demo (GIF or video), known limitations, deployment notes.
- `docs/adr/`: one short Architecture Decision Record for each non-obvious choice (concurrency style, embedding model, queue vs background tasks, deploy target).
- `docs/runbook.md` as described in section 9.
- Code comments explain why, not what. Public functions and Pydantic models carry docstrings.
- Keep `.env.example` complete and commented.

---

## 14. Working agreement for the coding agent

1. **Plan first for anything beyond a small fix.** State the approach, the files you will touch, and the risks, then implement in small increments.
2. **One concern per change.** Do not mix refactors with behavior changes. Do not expand scope. Agents, real store integrations, multi-language support, and sending without approval are out of v1 (spec sections 4 and 5).
3. **Verify before declaring done.** Run `make lint`, `make test`, and the relevant eval or resilience target. Report the actual results, including failures. Never claim a check passed without running it.
4. **Do not invent.** No made-up policy facts, prices, model names, library APIs, or test results. Look it up, read the code, or ask.
5. **Flag, don't absorb, contradictions** between spec, policies, and the request (section 0).
6. **Never commit secrets,** generated credentials, or large binaries. Never disable a safety check, lint rule, or test to get a build green.
7. **Commit hygiene:** small commits, imperative messages explaining why, no unrelated formatting churn.
8. **When uncertain about a safety-relevant behavior, choose the safer one** (escalate, block, ask the owner) and note the assumption.

---

## 15. Definition of done

A change is done when:

- [ ] It satisfies the relevant spec requirement (cite the FR or section).
- [ ] No safety invariant in section 1 is weakened.
- [ ] Types, lint, and tests pass locally and in CI.
- [ ] New behavior has tests, including at least one failure-path or adversarial test where relevant.
- [ ] Migrations (if any) are included and apply cleanly to an empty database.
- [ ] Logs, errors, and metrics for the new path exist and contain no personal data.
- [ ] Docs, runbook, `.env.example`, and ADRs are updated where affected.

The v1 release is done when the spec section 20 checklist holds and, additionally:

- [ ] `make resilience` passes.
- [ ] An external uptime monitor watches `/readyz` and alerts by email.
- [ ] The eval report is committed with model and prompt hashes.
- [ ] Zero ungrounded drafts reach the queue on the labeled set, and escalation recall is at least 0.95.
- [ ] Known limitations are documented honestly.
