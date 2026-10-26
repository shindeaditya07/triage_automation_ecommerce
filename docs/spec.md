# ReplyDesk: Grounded Support Triage for Small E-commerce Stores

**Spec version:** 1.1 (2026-10-15)
**Status:** Source of truth for the project. Any request that contradicts this file must be flagged, not silently obeyed.
**Supersedes:** spec.md v1.0 (2026-10-14). This version incorporates amendments A1-A19 from `spec-amendments.md`, all approved by the owner.
**Policy reference:** `policies.md` v1.1. Lock file SHA-256: must be validated by CI.

---

## 1. Overview

ReplyDesk is a human-in-the-loop support automation platform for small e-commerce merchants. It ingests customer messages from email and web forms, classifies intent and urgency, retrieves the relevant order records and store policy, and drafts replies in which every factual claim is traceable to its source. A deterministic grounding layer blocks any draft that commits to an action the data does not support. The owner approves or edits each reply before it is sent. The system measures its own accuracy through a labeled evaluation harness and exposes a metrics dashboard to track operational performance over time.

---

## 2. Context and Problem

Small online stores often have one person handling all customer messages. Slow or inaccurate replies about orders, refunds, and damaged items drive away repeat customers. The owner spends significant time on repetitive lookups — checking order status, reading policies — rather than on actual decisions.

Given incoming support messages, the system must:
- Classify and prioritize them by intent and urgency
- Retrieve the relevant order and policy information
- Prepare accurate, grounded replies
- Route borderline cases to the owner with full context

So the owner spends time only on decisions, not on repetitive lookups.

---

## 3. Goal

A pipeline that triages every ticket, drafts replies grounded in real order data and store policy, and escalates sensitive cases. The owner approves every outgoing reply.

---

## 4. Design Decision: Pipeline First, Agents Later

The original concept listed seven agents. For v1, only three steps need an LLM; the rest are deterministic code. Agent behavior (tool-calling loops, multi-step reasoning) is a v2 topic and only after the eval harness exists.

| Step | Type | Notes |
|---|---|---|
| Intake | Function | Normalize email and form payloads into one ticket schema. PII masking applied here. |
| Classification | **LLM** (structured output, temperature=0) | Intent, urgency, confidence, sentiment flags. |
| Pre-lookup escalation | Deterministic rules engine | Tone, legal keywords, injection signals, confidence, intent=other. |
| Context lookup | Function | Order, shipping status, customer history from store API. |
| Post-lookup escalation | Deterministic rules engine | Value, order-not-found, mismatch, policy windows, repeat count. |
| Policy retrieval | Retrieval (pgvector) | Top-k passages with stable policy IDs, plus deterministic passages by intent. |
| Drafting | **LLM** (structured output) | Uses only retrieved facts; returns text plus citations. |
| Grounding check | **Deterministic validator first**, LLM judge as second pass | Blocks unsupported claims, outcome promises, tone violations. |
| Approval queue | Owner action | Approve, edit (with warning), or reject. |
| Mock send | Function | Records the final text and who approved it. |
| Metrics + audit log | Function | Append-only record of all events. |

---

## 5. Scope

### In Scope (v1)

- Contact-form endpoint (public, rate-limited, honeypo-protected) and email-style JSON ingestion (HMAC-authenticated)
- Classification with the full intent set (section 11)
- Two-stage escalation (pre- and post-lookup)
- Context lookup, policy retrieval (pgvector), drafting with citations
- Grounding check: deterministic validator + LLM judge
- Approval UI: queue sorted by urgency, draft and evidence side by side, approve/edit/reject
- Owner-edit grounding warning with recorded override reason
- Mock store API with seeded edge cases (section 17)
- Labeled evaluation harness and eval scripts
- Metrics dashboard and append-only audit log
- PII masking at ingestion
- Data retention and erasure path (default 90 days)

### Out of Scope (v1)

- Sending without owner approval
- Voice or chat-widget support
- Payment processing or issuing real refunds
- Multi-language support (non-English messages are escalated)
- Real Shopify or WooCommerce integration (mock store only; adapter interface designed to allow it later)
- Agentic tool-calling loops (v2 topic)

---

## 6. Users

- **Store owner (primary):** reviews the ticket queue, approves or edits drafts, handles escalations, monitors the dashboard.
- **Customer (indirect):** sends messages and receives approved replies. Never interacts with the ReplyDesk system directly.

---

## 7. Core Flow

```
message in
  → PII masking → normalize → dedupe/thread
  → classify (LLM)
  → pre-lookup escalation check
    (→ escalated? route to owner, no draft)
  → fetch order context
  → post-lookup escalation check
    (→ escalated? route to owner; high-value/outside-policy: suggestion-only draft, no one-click approval)
  → retrieve policy passages (pgvector + deterministic by intent)
  → draft with citations (LLM)
  → grounding check (deterministic validator → LLM judge)
    (→ fail? retry once → fail again? fallback template + flag)
  → approval queue
  → owner: approve / edit (with grounding warning) / reject
  → mock send
  → audit log + metrics
```

Grounding failures after two attempts produce a safe generic template and flag the ticket for the owner.

---

## 8. Functional Requirements

| ID | Requirement |
|---|---|
| FR-1 | Accept messages via a public contact-form endpoint (rate-limited, size-limited, honeypot-protected) and an email-style JSON endpoint (HMAC/API-key authenticated). Normalize both into the ticket schema (section 11). |
| FR-2 | Deduplicate and thread: a repeat reply from the same customer on the same order attaches to the existing ticket using `in_reply_to`. Ingestion is idempotent on `external_message_id`. |
| FR-3 | Classify each ticket and return `intent`, `urgency`, `confidence`, `sentiment`, and flags as JSON (structured output, temperature=0). |
| FR-4 | Apply pre-lookup escalation rules (section 14.1) immediately after classification. |
| FR-5 | Extract an order ID from the message text, or match by sender email when none is given. |
| FR-6 | Look up order, shipping status, and customer history from the store API. Snapshot the order data with the draft. |
| FR-7 | Apply post-lookup escalation rules (section 14.2) after context is fetched. |
| FR-8 | Embed `policies.md` passages and FAQ in pgvector; retrieve top-k passages plus deterministic passages by intent. Every cited policy ID must exist in the loaded policy set. |
| FR-9 | Draft a reply with the LLM receiving only retrieved, normalized facts; output JSON with `reply_text`, `citations[]`, `proposed_action`, and `confidence` (section 12). |
| FR-10 | Run the grounding check (section 13) on every draft before it reaches the queue. Retry once with failure reasons; use fallback template on second failure. |
| FR-11 | Approval UI: queue sorted by urgency; draft and evidence (cited order fields, policy passages, flags, grounding result) displayed side by side; approve, edit, or reject. |
| FR-12 | Re-run the deterministic grounding validator on owner-edited text as a non-blocking warning. Override requires a recorded reason in the audit log. |
| FR-13 | Mock send on approval; record final text, diff from draft (if edited), approver, and timestamp. |
| FR-14 | Metrics (section 18): draft acceptance rate, `time_to_draft`, `time_to_send` (business-hours-aware), grounding failure rate and rule breakdown, escalation rate, cost per ticket (tokens and INR). |
| FR-15 | Append-only audit log of every ticket event, draft, edit, approval, rejection, and mock send, with actor and timestamp. |
| FR-16 | Mask card numbers, bank account numbers, UPI IDs, and government-ID-like patterns in message bodies before storage and before any LLM call. Never log or store card numbers. |
| FR-17 | Data retention: a scheduled job soft-deletes ticket data after the configured period (default 90 days). Customer erasure requests are honoured per the retention config. |

---

## 9. Non-Functional Requirements

- **Safety:** nothing is sent without owner approval; low confidence and `other` intent go to the owner, never to a guess.
- **Privacy:** customer personal data is used only within its own ticket's context. Data from one ticket never appears in another ticket's context, draft, log, or error message.
- **Auditability:** every draft is reproducible from stored inputs (prompt version, prompt hash, model, retrieved passage IDs, order snapshot, policy version).
- **Cost:** track tokens and cost in INR per ticket; prices live in config, not code.
- **Latency:** target under 10 seconds from ingestion to draft in the queue. Each pipeline stage records its duration.
- **Resilience:** the API starts even when the database is down; connection pool uses `pool_pre_ping=True`, `pool_recycle=1800`, `pool_timeout=10`. Background jobs survive restarts via a DB-backed job table.
- **Observability:** structured JSON logs with `request_id`; ticket-scoped lines with `ticket_id` and `stage`. The runbook documents what can go down, how it appears, and how to recover.
- **Local-first:** the full stack runs with one `docker compose up` from a clean clone.

---

## 10. Stack

- **Frontend:** React + TypeScript (strict mode), TanStack Query, Tailwind CSS, API types generated from OpenAPI schema (not hand-written)
- **Backend:** FastAPI (Python 3.12, `uv` or `venv`, never Conda)
- **Database:** Postgres with pgvector
- **Mock store:** FastAPI service with seeded data; adapter interface designed for real store integration later
- **LLM:** any provider with JSON-schema structured output, behind one `LLMClient` interface (classify, draft, judge, embed); provider adapters are interchangeable for evals
- **Job runner:** DB-backed job table (`SELECT ... FOR UPDATE SKIP LOCKED`); recorded in `docs/adr/0002-job-runner.md`
- **Deployment target:** Docker Compose locally; Render (or equivalent) for the demo with managed Postgres + pgvector; frontend served as static assets; single origin behind a reverse proxy preferred

---

## 11. Ticket Schema

```json
{
  "ticket_id": "uuid",
  "external_message_id": "string (idempotency key)",
  "thread_id": "uuid",
  "in_reply_to": "string|null",
  "channel": "email | form",
  "received_at": "ISO-8601 UTC",
  "customer": {
    "email": "string (hashed in logs)",
    "name": "string|null"
  },
  "subject": "string|null",
  "body": "string (PII-masked before storage)",
  "order_id_hint": "string|null",
  "attachments": [
    { "filename": "string", "content_type": "string", "size_bytes": 0 }
  ],
  "has_photo": "boolean (at least one image attachment)",
  "status": "new | classified | drafted | escalated | approved | sent | rejected | failed",
  "classification": {
    "intent": "order_status | refund | return | damaged_item | product_question | cancellation | address_change | payment | warranty | other",
    "urgency": "low | normal | high | critical",
    "confidence": 0.0,
    "sentiment": "neutral | negative | very_negative",
    "flags": [
      "angry", "abusive", "legal_threat", "suspected_fraud",
      "prompt_injection_suspected", "repeat_complaint",
      "order_mismatch", "compensation_request", "high_value"
    ]
  },
  "escalation_reason": "string|null"
}
```

**Notes:**
- `high_value` is set by the **rules engine after order lookup**, not by the classifier (amendment A2).
- `status = "failed"` means the pipeline could not complete after retries; the ticket is visible to the owner with a reason (amendment A7).
- `attachments` contains metadata only; image content is not analyzed in v1 (amendment A7).
- `has_photo` determines whether DMG-3.1 photo evidence is present for the grounding validator.

---

## 12. Draft Output Schema

```json
{
  "reply_text": "string",
  "citations": [
    {
      "claim": "string",
      "source_type": "order_field | policy",
      "source_ref": "orders.delivered_at | RET-1.1"
    }
  ],
  "proposed_action": "none | request_information | return_instructions_suggested | cancellation_suggested | address_change_suggested | store_credit_suggested | refund_suggested | replacement_suggested | escalate",
  "confidence": 0.0,
  "prompt_version": "string",
  "prompt_hash": "string",
  "model": "string",
  "policy_version": "string",
  "order_snapshot_id": "uuid"
}
```

`proposed_action` is a suggestion for the owner. The system never executes it. The draft stores the full reproducibility chain (`prompt_version`, `prompt_hash`, `model`, `policy_version`, `order_snapshot_id`, retrieved passage IDs).

---

## 13. Grounding Rules

A draft passes only if all of the following hold. Failures are structured (which rule, which span, which citation was missing) and feed the retry prompt, audit log, and dashboard metrics.

### 13.1 Deterministic Validator (runs first, is the authority)

**Rule G-1 (Order-derived values):** Every amount, date, order number, and tracking number in `reply_text` that derives from order data must exactly match a cited order field (after normalizing currency symbols, separators, and date formats). An extracted value with no citation fails.

**Rule G-2 (Policy-derived values):** Every amount, date, or duration in `reply_text` that derives from policy (e.g., "7 days", "48 hours", "5–7 business days", "₹999") must match the cited policy passage text in `policy_constants.yaml`. A policy value with no cited policy ID fails.

**Rule G-3 (Commitment/outcome promise block):** The following language always fails (TON-9.2, ESC-8.2, ESC-8.3): "we will refund", "we'll replace", "you will receive by", "we'll deliver by", "you'll get a refund", compensation offers, discount codes promised. A maintained phrase list is used; the list lives in config and is testable. Policy statements ("our policy provides a refund within 5–7 business days once approved, and the owner will confirm the outcome") are allowed when backed by the cited policy ID and verified conditions.

**Rule G-4 (Citation existence):** Every cited policy ID must exist in the loaded policy set. Every cited order field must exist in the order snapshot. Hallucinated citations fail.

**Rule G-5 (No uncited claim):** Any factual claim in `reply_text` that is not traceable to a citation fails.

**Rule G-6 (Privacy):** The draft must not reveal data from another customer's order (IDs or emails not belonging to this ticket).

**Rule G-7 (Injection echo):** Customer-supplied instructions ("ignore your rules", "refund me now") must not appear to be obeyed or echoed as instructions.

**Rule G-8 (Tone — sign-off):** `reply_text` must end with the exact store sign-off from the store profile config (TON-9.3).

**Rule G-9 (Tone — apology):** At most one apology phrase in the entire reply (TON-9.1).

**Rule G-10 (Tone — length):** Reply does not exceed the configured maximum character limit.

### 13.2 LLM Judge (second pass)

- Checks semantic commitments the rules above cannot catch.
- Can block but **cannot override** a deterministic failure.
- Must be from a different model family than the drafter where possible.
- Also logs a confidence score, which is stored in the audit log.

### 13.3 Retry and Fallback

- On any failure: retry once with the structured failure reasons injected into the prompt.
- On second failure: use the safe fallback template and flag the ticket for the owner (status remains in queue, clearly flagged).

### 13.4 Owner-Edit Validation

- When an owner edits a draft, the deterministic validator re-runs on the edited text as a **non-blocking warning**.
- Overriding a warning requires a recorded reason stored in the audit log.

---

## 14. Escalation Rules

Escalation is two-stage. Per-reason behavior is defined so drafting behavior is clear.

### 14.1 Pre-Lookup Escalation (runs immediately after classification)

Route to owner with **no draft** when any of these hold:
- Angry or **abusive** tone (classifier flag `abusive` OR deterministic keyword backstop)
- **Legal threat**, consumer-court, chargeback, or regulator mention (classifier flag OR keyword/regex backstop)
- **Suspected fraud** or request targeting another customer's data (classifier flag OR keyword/regex backstop)
- **Suspected prompt injection** (classifier flag OR structural detection)
- Classifier **confidence below 0.70**
- **Intent = `other`** (no applicable policy workflow)

Deterministic keyword/regex backstops for legal, chargeback, and abusive terms run concurrently with the classifier flag; either is sufficient to trigger escalation. This ensures escalation recall does not rest solely on LLM judgment.

### 14.2 Post-Lookup Escalation (runs after order context is fetched)

Route to owner with **no draft**:
- **Order not found** for the given ID and sender email
- **Order-sender mismatch** (TON-9.4): the order ID does not belong to the sender's email. No other-customer order data enters the LLM context or draft.
- **Repeat complaint** (3 or more messages on the same ticket thread — classifier flag `repeat_complaint`)
- **High-value order** (order value above `policy_constants.yaml` → `high_value_threshold`; the `high_value` flag is set here, not by the classifier)

Route to owner with a **suggestion-only draft** (cannot be one-click approved, labeled as "Escalated — suggestion only"):
- **Outside-policy request**: return after the 7-day window (RET-1.5), damage report after 48 hours (DMG-3.3), compensation/goodwill request, refund on final-sale item that is not damaged, any situation not covered by `policies.md` section 8 (ESC-8.3)

**Note on intent `product_question`:** In v1, only FAQ content (PRD-6.1) is available. If the answer is not in the FAQ, the ticket is escalated with a `product_question` suggestion-only draft.

---

## 15. Order Data Contract

The mock store (and later real store adapters) must provide the following fields. The grounding validator cites only fields in this contract. See `docs/data-contract.md` for the full schema.

**Order:**
- `order_id`, `customer_email`, `status` (placed / dispatched / delivered / cancelled / failed_delivery)
- `placed_at` (UTC timestamp), `dispatched_at` (UTC|null), `delivered_at` (UTC|null)
- `payment_method` (upi | card | netbanking | cod)
- `order_value_paise` (integer, paise)
- `shipping_charge_paise` (integer)
- `shipping_tier` (metro | standard | remote)
- `cancellation_record` (null | {cancelled_at, cancelled_by, reason})
- `refund_record` (null | {refund_id, issued_at, amount_paise, method})
- `courier_tracking_number` (string|null)
- `courier_eta` (ISO-8601 UTC|null — only if courier has provided a new ETA per SHP-4.5)
- `failed_delivery_attempts` (integer)

**Items (array per order):**
- `item_id`, `product_name`, `quantity`, `unit_price_paise`
- `item_flags`: array of `"final_sale" | "consumable" | "personalized" | "gift_card" | "small_appliance"`
- `discount_applied_paise`, `store_credit_used_paise`

**Damage Report (null | object):**
- `reported_at` (UTC timestamp), `has_photo` (boolean), `description` (string)

**Customer History:**
- `customer_email` (used as key), `total_orders`, `prior_complaint_count`, `last_order_id`

---

## 16. Security and Prompt Injection

- Customer message text is **untrusted data**, not instructions. It is passed to the LLM inside clearly delimited data blocks (e.g., `<customer_message>...</customer_message>`) with an explicit statement that block content is data.
- Delimiter strings found in customer text are neutralized before insertion.
- The drafting step has no tools and cannot trigger refunds, lookups, or other side effects.
- Inbound email and form content is sanitized before processing: strip HTML markup, links, and hidden text.
- Log suspected prompt injections with the `prompt_injection_suspected` flag; show the original message to the owner in the UI.
- Order lookup takes `(order_id, sender_email)`; on mismatch only a private flag is returned — no order data enters the LLM context.
- PII masking: card numbers, bank account numbers, UPI IDs, and government-ID-like patterns are masked before storage and before any LLM call. Card numbers are never stored.

---

## 17. Mock Store: Required Edge Cases

The seeded mock store must include all of these. Dates are generated relative to a frozen clock in tests.

| # | Scenario | Key policy reference |
|---|---|---|
| 1 | Order delivered on time, inside the 7-day return window | RET-1.1 |
| 2 | Order delivered, return window expired by exactly 1 day | RET-1.5 |
| 3 | Order shipped, in transit, within the estimated delivery window | SHP-4.2 |
| 4 | Order shipped, late beyond the estimated window, no courier ETA | SHP-4.5 |
| 5 | Order shipped, late, courier has provided a new ETA | SHP-4.5 |
| 6 | Order not yet shipped (cancellable under CAN-5.1) | CAN-5.1 |
| 7 | Order already cancelled | CAN-5.1 |
| 8 | Order delivered with a damage report inside 48 hours (with photo) | DMG-3.1, DMG-3.2 |
| 9 | Damage report with no photo attached | DMG-3.4 |
| 10 | Damage report after the 48-hour limit | DMG-3.3 |
| 11 | Final-sale item return request (not damaged) | RET-1.4, ESC-8.3 |
| 12 | Consumable / personalized / gift-card item return request | RET-1.4 |
| 13 | Partial refund: one item of a multi-item order | REF-2.4 |
| 14 | Order with a refund already issued | REF-2.6 |
| 15 | COD order refund request (≤ ₹5,000) | REF-2.3, PAY-7.1 |
| 16 | High-value prepaid order above ₹10,000 escalation threshold | ESC-8.1 |
| 17 | Order with wrong address (customer error) | SHP-4.6 |
| 18 | Order ID does not exist | FR-5 |
| 19 | Order ID belongs to a different customer (privacy test) | TON-9.4, ESC-8.2 |
| 20 | Customer with multiple orders and an ambiguous message | FR-5 |
| 21 | Repeat customer with prior complaints (3+ messages on thread) | ESC-8.2 |
| 22 | Duplicate message submission (idempotency test) | FR-2 |
| 23 | Customer replying on an existing thread | FR-2 |
| 24 | Failed delivery after 3 attempts, returned to warehouse | SHP-4.7 |
| 25 | Store-credit order and refund-to-store-credit scenario | REF-2.5 |
| 26 | Remote pin code (up to 10 business days) | SHP-4.2 |
| 27 | Sunday/public holiday order (next business day dispatch) | SHP-4.1 |
| 28 | Missing photo follow-up: customer sends photo in thread reply | DMG-3.4 |
| 29 | Non-English message (must escalate; language detection out of scope) | FR-3, ESC-8.3 |
| 30 | Multi-intent message (e.g., order status + return request) | FR-3, A3 |
| 31 | Warranty claim within 6 months on small appliance | PRD-6.3 |

**Note:** COD orders in the seed data never exceed ₹5,000 (PAY-7.1). Case 16 is always a prepaid order.

---

## 18. Metrics Dashboard (v1)

| Metric | Definition |
|---|---|
| Tickets by intent and urgency | Count breakdown, live |
| Draft acceptance rate | Final text equals draft (after whitespace normalization) / total approved |
| `time_to_draft` | Ingestion timestamp to draft-in-queue timestamp (system time, all hours) |
| `time_to_send` | Draft-in-queue to mock-send timestamp, business-hours-aware (Mon–Sat 10:00–18:00 IST) |
| Grounding failures caught | Count by rule (G-1 through G-10), not resetting on restart |
| Escalation rate | Escalated tickets / total, by escalation reason |
| Cost per ticket | Tokens and INR; prices from config |
| Fallback template rate | Tickets that used the fallback after two grounding failures |
| Policy coverage | Which policy IDs were cited this period |

All metrics are computed from stored data, not ad-hoc counters that reset on restart.

---

## 19. Evaluation Plan

Evals are built before features.

### Datasets

- **60–100 labeled synthetic tickets** covering all intents, urgency levels, and adversarial cases (prompt injection in body/subject/HTML, delimiter-breaking text, another customer's order ID, mixed intents, vague messages, angry customers, missing order ID, oversized input, unicode tricks in amounts and dates, non-English messages).
- **Ground truth per ticket:** intent, urgency, expected escalation (yes/no + reason), which policy IDs apply, whether the ticket should produce a draft or no-draft.
- **Urgency rubric** in `evals/urgency-rubric.md` so labels are consistent.
- **Seeded bad-draft corpus** (target 100+ drafts) with known grounding violations; the validator must catch every one. Also measure false blocks on good drafts.
- **Policy ID coverage matrix:** every policy ID must appear in at least one labeled ticket. CI fails if a label references a policy ID absent from `policies.md`.

### Metrics

| Metric | Target (v1) |
|---|---|
| Per-intent precision and recall (all 10 intents) | Report all; goal ≥ 0.90 on `order_status` and `refund` |
| Keyword classifier baseline comparison | LLM classifier must outperform on every intent |
| Escalation recall (should-escalate cases caught) | ≥ 0.95 |
| Grounding failure rate (unsupported claim reaches queue) | Target near 0% |
| Draft acceptance rate | Report; no hard target; trend matters |
| `time_to_draft` (p50, p95) | Report |
| `time_to_send` (business-hours-aware, p50) | Report |
| Cost per ticket (tokens and INR) | Report |
| Wilson confidence intervals | Shown alongside all precision/recall numbers |

### Process

- Version prompts (hash each prompt file before eval run), record the model and temperature.
- Each eval run stores model, prompt hashes, and results in `evals/results/YYYY-MM-DD-<run-id>/`.
- Labels are written or reviewed by a human. Do not generate eval labels with the model being evaluated.
- Report all numbers including bad ones. Do not suppress failing metrics.

---

## 20. Time Semantics

All timestamps are stored as **timezone-aware UTC**.

Policy window evaluation is done in **Asia/Kolkata (IST)**:
- **Return window (RET-1.1):** ends at 23:59:59 IST on `delivery_date + 7 calendar days`. "Calendar days" means day of week is irrelevant.
- **Damage window (DMG-3.1):** ends exactly 48 hours after the `delivered_at` UTC timestamp, converted to IST for display.
- **Business days (REF-2.2, SHP-4.1, SHP-4.2):** Monday–Saturday, excluding a configured public holiday list. Implementation in one module with boundary tests (exactly 7 days, exactly 48 h, 1 second over each window).
- **Support hours (FAQ-10.6):** Monday–Saturday 10:00–18:00 IST.

Never read `now` inside business logic — inject a clock so tests can freeze it.

---

## 21. Build Environments

| Environment | Purpose | Notes |
|---|---|---|
| **Local** | Development and fast iteration | `docker compose up`; `.env` from `.env.example` |
| **CI** | Automated validation on every PR | GitHub Actions; no live LLM calls; uses recorded fixtures; fake mail |
| **Staging** | Pre-production integration and manual review | Separate DB; limited seed data; owner can run evals against staging |
| **Production** | Live demo | Latest tagged release; managed Postgres + pgvector; external uptime monitor on `/readyz` |

Environment variables govern all environment differences. No local file paths in code. See `.env.example` for a complete, commented list of all required variables.

---

## 22. Definition of Done (v1)

- [ ] One `docker compose up` starts everything from a clean clone.
- [ ] `make lint`, `make test`, `make eval`, and `make resilience` all pass.
- [ ] Zero ungrounded drafts reach the approval queue on the labeled set.
- [ ] Escalation recall ≥ 0.95 on the labeled set.
- [ ] README shows: architecture diagram, how to run, eval results table (with date, model, and prompt hashes), short demo (GIF or video), known limitations, deployment notes.
- [ ] An external uptime monitor watches `/readyz` and alerts by email.
- [ ] Eval report committed with model and prompt hashes.
- [ ] `docs/runbook.md` documents what can go down, how to detect it, and how to recover.
- [ ] All mock-store edge cases (section 17) are covered by at least one labeled ticket.

---

## 23. Open Questions (Resolved with v1.1)

All amendments A1-A19 have been incorporated. Residual open items:

1. **LLM provider and models:** confirm primary drafter and judge (must be different families) for the initial eval run. Record in `docs/adr/0003-llm-provider.md`.
2. **Holiday list:** provide the public holiday list for business-day calculations (A9). Default: Gazetted holidays of India 2026. Configurable.
3. **Product catalog (A10):** v1 uses FAQ-only for `product_question`; any query without an FAQ answer is escalated. Product catalog is a v2 addition.
4. **Retention and DPDP compliance (A13):** default 90 days. DPDP Act compliance should be verified by a qualified professional before real-customer deployment.
