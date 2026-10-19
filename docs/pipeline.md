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
- Whether a *valid* classification still needs the owner (low confidence, a legal threat, intent
  `other`) is decided by the next step, pre-lookup escalation.

## Not wired yet

`make_process_ticket_handler` (`services/api/pipeline/process.py`) exists but the app does not
register it: it needs a live `LLMClient`, which needs API keys, and the later steps. Until the
pipeline is complete, queued jobs have no handler.
