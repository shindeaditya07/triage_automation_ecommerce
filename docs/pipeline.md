# Ticket pipeline

`process_ticket` is the job intake queues for every new ticket. Steps are added one branch at a
time; each moves the ticket forward only from the state it expects, so a job that runs twice
changes nothing the second time.

## Step 1: classification (`services/api/pipeline/classify.py`)

- Sends the cleaned subject and body (intake has already masked PII and removed hidden text) to
  `LLMClient.classify`. The prompt (`services/api/prompts/classify.md`) puts them in tagged blocks
  with `& < >` escaped, so message text cannot close a block.
- A valid answer is stored on the ticket (`intent`, `urgency`, `confidence`, `sentiment`,
  `flags`), status `new` -> `classified`, with an audit event `ticket.classified` that holds the
  call record (model, prompt hash and version, tokens, latency, INR cost).
- **Invalid output** is tried once more with the field errors (never the values) put in front of
  the model. A second invalid answer escalates the ticket.
- **Any other model failure** (provider down, circuit open, rejected request) escalates at once:
  `status = escalated`, `escalation_reason = llm_error:<ClassName>`, audit event
  `ticket.escalated`, whose `error` field holds the failure's own message (for a rejected
  request, the HTTP status and Google's short error category). The same text goes in the log.
  The owner sees the original message; no draft is made.
- The model may not set `high_value`: that flag comes from the order's value after the order is
  read. If the model returns it, it is removed and listed in the audit event as `dropped_flags`.
- Whether a *valid* classification still needs the owner is decided by step 2.

## Step 2: pre-lookup escalation (`services/api/escalation/pre_lookup.py`)

Runs right after classification, with no model call and no order lookup. The ticket goes to the
owner with **no draft** when ANY of these holds (spec 14.1):

| Reason | Trigger |
|---|---|
| `abusive`, `legal_threat`, `suspected_fraud` | the classifier flag OR the keyword backstop (ESC-8.2) |
| `prompt_injection` | the classifier flag OR the structural scan (`escalation/injection.py`) |
| `low_confidence` | confidence strictly below 0.70 |
| `intent_other` | intent is `other` |

Two independent sources back the first four, so a flag the model missed is still caught. Flags
the deterministic checks found are added to the ticket's flags. All reasons that apply are
recorded, comma-joined, in `escalation_reason`, and the audit event `ticket.escalated` (stage
`pre_lookup`) lists the backstop rules and injection signals that matched. The classification is
kept on the escalated ticket so the owner sees it with the original message.

The structural scan looks for phrases an ordinary customer has no reason to write: our own
delimiter tags, chat-template tokens, a `system:` turn, "ignore your instructions", "you are now
in admin mode", asking for the prompt or credentials. Like the backstop it leans towards
escalating; a false match costs the owner one read.

The handler works from the ticket's state, so a job that stopped between the two steps resumes
at the escalation check without a second model call.

## Step 3: order lookup (`services/api/context/`)

Runs for a ticket that passed step 2. It finds the order the ticket is about, fetches it from the
store (`GET /orders/{order_id}?email=<sender>`, explicit timeout) and records what it found.

1. **Candidates:** order IDs in the form field, subject and body (`RD-10001`, also `rd10001`,
   `RD 10001`, look-alike characters). A bare number is never treated as an order ID.
2. **One candidate:** ask the store for it with the sender's address.
3. **Several candidates:** do not guess (`ambiguous`).
4. **None named:** a sender with exactly one order on file gets that order; several orders is
   `ambiguous`; none, or an unknown sender, is `no_order`.

| Result | Meaning | What is kept |
|---|---|---|
| `found` | the order exists and belongs to the sender | a snapshot row in `order_snapshots` (every draft will reference it) |
| `not_found` | the id does not exist | the id the customer wrote |
| `mismatch` | the id belongs to another address | the id, and the `order_mismatch` flag. **No order data**, here or in the audit trail |
| `ambiguous` | several possible orders | how many candidates |
| `no_order` | nothing to look up | nothing |

The summary (result, id, candidate count, snapshot id, the customer's order and complaint counts)
is stored in `tickets.order_lookup` (migration 0004) and in the `ticket.order_lookup` audit event.
`order_lookup` is null until the step has run, which is how a retried job knows not to repeat it.
What each result means for the ticket is step 4.

If the store cannot be reached (timeout, connection error, 5xx) the job fails and is retried with
the job runner's backoff; after its attempts it goes dead and the ticket shows as failed with the
reason. A store reply that breaks the data contract fails the job at once, since retrying cannot
fix it.

## Step 4: post-lookup escalation (`services/api/escalation/post_lookup.py`)

Runs once the lookup is recorded, with no model call (spec 14.2). The handler rebuilds the lookup
from the ticket and its order snapshot, so a job that stopped after step 3 resumes here.

**No draft, to the owner** (`status = escalated`, stage `post_lookup`; every reason that applies
is recorded, comma-joined):

| Reason | Trigger |
|---|---|
| `order_not_found` | the lookup result is `not_found` |
| `order_mismatch` | the lookup result is `mismatch` (flag already set by step 3) |
| `repeat_complaint` | the ticket is the 3rd or later message on its thread (ESC-8.2, counted in the `tickets` table, `repeat_complaint_message_count`); the flag is set here |
| `high_value` | order value strictly above `high_value_threshold_paise` (ESC-8.1); the flag is set here, never by the model |

**Suggestion only** (ESC-8.3): the ticket stays `classified` and `escalation_reason` is set to
`outside_policy`, which is how the draft step will know to make a draft the owner cannot
one-click approve. Audit event `ticket.suggestion_only` lists the triggers:

| Trigger | Rule |
|---|---|
| `return_window_closed` | intent `return` or `refund`, delivered order, no refund yet, asked after 23:59:59 IST on the 7th calendar day after delivery (RET-1.1, RET-1.5) |
| `damage_window_closed` | intent `damaged_item`, report more than 48 hours after the delivery timestamp (DMG-3.1, DMG-3.3) |
| `compensation_request` | the classifier's `compensation_request` flag |
| `final_sale_not_damaged` | intent `return` or `refund`, an item flagged final sale, no damage report. In a multi-item order the rule cannot tell which item the customer means, so it errs towards the owner |

Everything else is drafted. An order that could not be pinned down (`ambiguous`, `no_order`) is
not an escalation: the draft asks the customer which order they mean. A product question the FAQ
cannot answer is also outside policy, but no order field shows that and a similarity score cannot
either (see `docs/retrieval.md`), so the drafting step decides it from the passages it was given.

No-draft reasons win over outside-policy ones. The windows (`services/api/time_utils.py`) judge the
moment the customer wrote (`received_at`), not when the job runs, so a retry hours later cannot
change the answer; the functions take that moment as an argument and read no clock.

## Step 5: drafting (`services/api/pipeline/draft.py`)

Runs for every ticket the routing did not send to the owner without a draft: the normal route and
the suggestion-only one.

1. **Retrieve** the policy passages (`docs/retrieval.md`). A suggestion-only ticket also gets
   ESC-8.3. A failure to embed escalates with the model's reason; an index missing a required
   passage escalates as `retrieval_failed`.
2. **Ask the drafter** for a reply. It sees the customer's subject and message in data blocks,
   this customer's order (the sender's email address is removed), the passages, and the store's
   sign-off. Nothing else.
3. **An invalid answer is tried once more** with the field errors put back in front of the model.
   Two invalid answers in a row escalate as `draft_invalid_output`; any other model failure
   escalates with its own reason.
4. **Check the draft is grounded** (`docs/grounding.md`): the deterministic rules, then the judge.
   A draft that fails is asked for once more with the failures listed in front of the model (rule,
   reason and the offending words, never customer data). At most two drafts are ever requested.
5. **A second failure uses the fallback template** (`fallback_template` in
   `grounding_config.yaml`): a short fixed reply with the store's sign-off that states no facts and
   makes no promise. The rules are run on it before it is used; if they ever fail it, the ticket
   escalates as `fallback_template_invalid` instead. An invalid answer after a grounding failure
   also falls back. A judge that cannot answer escalates as `grounding_unavailable`, because a
   second draft would not help.
6. **Store the draft** (`drafts`) with what it was made from: the order snapshot, the passage IDs,
   the prompt version and hash, the model, the policy version, the token counts and the grounding
   result. These come from this step's own inputs, not from what the model reports. `attempt` is 1
   or 2 for a model draft and 3 for the fallback (`kind = fallback_template`). The ticket moves
   from `classified` to `drafted`. Audit events: `draft.attempt_failed` for each failed attempt
   (outcome, rule IDs and reasons, never the rejected text), `draft.fallback_used` when the
   template was used (this is the owner-visible flag: the queue marks any draft whose `kind` is
   `fallback_template`), and `ticket.drafted`. A suggestion-only draft is marked `suggestion_only`
   and the ticket keeps `escalation_reason=outside_policy`.

Every draft in `drafts` has been checked. Nothing in the app reads drafts yet.

Without a `DraftingConfig` the handler stops after routing; the earlier steps' tests rely on that.

## Registered in the app (`services/api/pipeline/wiring.py`)

On startup the app builds the handler from the environment: the model from `LLMSettings`, the order
store from `MOCK_STORE_URL`, the policy index from the database, and the store's sign-off. It does
this only when the app owns its database (tests that pass an engine or their own handlers are
untouched) and only when `LLM_PROVIDER` is not `fake`. If the model is not configured, or its
settings do not validate, the app still starts, logs `pipeline disabled` with the setting names (never
values), and `process_ticket` jobs fail with "no handler". Drafts are stored for the owner to
review; nothing is sent to a customer. Staging setup: `guides/pipeline-staging.md`.
