# 0003. Anthropic drafts, OpenAI judges and embeds

- Status: proposed (the owner confirms the provider choice before any live key is used)
- Date: 2026-10-19

## Context

Three jobs need a model: classify and draft a reply, judge whether a draft is grounded in the
order record and the policy text, and embed policy passages for retrieval. Two risks shape the
choice:

- A model that grades its own output shares its blind spots. A judge from a different provider
  fails differently from the drafter, so it catches more.
- Anthropic offers no embeddings API, so embeddings must come from another provider anyway.

## Decision

- **Drafter (classify, draft):** Anthropic Messages API, structured output as a forced tool call.
- **Judge:** OpenAI Chat Completions, structured output as a JSON schema.
- **Embeddings:** OpenAI `text-embedding-3-small` at 1536 dimensions (the width of
  `policy_embeddings.embedding`). The OpenAI key used for the judge is reused unless
  `EMBED_API_KEY` is set.
- **Models are settings, not code.** `LLM_MODEL` and `JUDGE_MODEL` hold the model IDs, so changing
  one is an environment change plus a fresh eval run. Prices (INR per 1M tokens) are settings too.
- **Same provider for both is refused at startup** unless `JUDGE_SAME_FAMILY_OK=true`. A weaker
  judge is allowed, but only on purpose.
- **No SDKs.** The adapters speak plain HTTPS through `httpx`, so there is one dependency, one
  timeout and one error mapping, and tests use `httpx.MockTransport`.
- **Everything goes through `ModelGateway`** (`services/api/llm/gateway.py`), which adds:
  prompt version and SHA-256 on every call, bounded retries with jittered backoff on 429, 5xx,
  timeouts and connection errors only, a circuit breaker per model (opens after
  `LLM_BREAKER_FAILURES` failed calls in a row, one probe after the cooldown), schema validation
  of every answer, and a `CallRecord` with tokens, latency and cost.
- **Failure means escalation.** Every failure is an `LLMError` whose `escalation_reason` is
  `llm_error:<ClassName>`. A model failure never produces a draft.
- **Temperature** is sent as configured (default 0). Set `LLM_TEMPERATURE=` empty for a model that
  rejects the parameter.
- **Prompts** live in `services/api/prompts/*.md`. Data (customer text, order, policy) goes in
  tagged blocks in the user message with `& < >` escaped, so content cannot close a block.
- `LLM_PROVIDER=fake` (the default, and what CI and staging use) makes no network call.

## Consequences

- Two vendors, two keys, two bills, and an outage at either affects one step: drafting and
  classifying escalate when Anthropic is down; grounding checks escalate when OpenAI is down.
- The adapters are tested against recorded-shape mock responses, not live APIs. The first live run
  (`make eval` once classification exists) is also the first proof they work, and may need small
  fixes to the request shapes.
- Customer text leaves the system for two providers. Customer addresses and order details are sent
  only where the step needs them; the classifier sees text only.
- Changing provider later means one new adapter in `services/api/llm/providers/`.
