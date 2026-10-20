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
  `ticket.escalated`. The owner sees the original message; no draft is made.
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

## Not wired yet

`make_process_ticket_handler` (`services/api/pipeline/process.py`) exists but the app does not
register it: it needs a live `LLMClient`, which needs API keys, and the later steps. Until the
pipeline is complete, queued jobs have no handler.
