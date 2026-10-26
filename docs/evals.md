# Evaluations

Two runs, one report format. Both measure the same labeled tickets (`evals/datasets/labeled.jsonl`,
78 synthetic tickets) and the same 40 seeded bad drafts, and both save under
`evals/results/YYYY-MM-DD-<run-id>/` with the model, prompt hashes and policy version recorded.

| | `make eval-smoke` | `make eval MAX_COST_INR=100` |
|---|---|---|
| Model | none; replays recorded classifications | the real drafter, judge and embedder from your `.env` |
| Cost | none | real money, bounded by the limit |
| Runs in CI | yes | no |
| What it proves | the harness and the rules work | how the model performs |

## What the live run does

For each ticket it runs the production code: the classifier, the pre-lookup rules, the order
lookup (against the seeded mock store at the frozen clock `2026-10-15T12:00Z`), the post-lookup
rules, policy retrieval, the drafter, the ten grounding rules and the judge, with the retry and the
fallback template. A model failure at any step escalates the ticket, exactly as in production, and
counts as an escalation in the numbers. No database is used; policy embeddings are built in memory.

It reports everything the smoke run does, using the live classifier, and adds:

- how often the model's draft passed grounding on the first try and on either try
- how often the run fell back to the standard template
- how often drafting escalated the ticket instead
- grounding failures by rule (every failed attempt, not just the last)
- cost in rupees (total, per ticket, and the one-off policy embedding) and seconds per ticket

The keyword baseline runs alongside as the floor to beat. The live run fails if escalation recall
is below 0.95, the same floor as the smoke run.

## Cost control

- `--max-cost-inr` is required. The run stops the moment spend passes it, saves nothing, and exits
  with code 3. Spend is the sum of the call costs the gateway records.
- The four `*_PRICE_INR_PER_1M` settings must be filled in. With them at zero spend would always
  read as Rs 0 and the limit could never trigger, so the run refuses to start.
- `--limit N` (or `make eval LIMIT=5`) runs the first N tickets, to check keys and prices before
  a full run. A trial is not a result and its report says nothing about the whole dataset.

## Rate limits and invalid runs

A rate limit is not a model failure, so a ticket the provider failed (a rate limit, an outage, a
timeout, an open circuit breaker, a rejected request, or a judge that gave no verdict) is counted
separately as a **provider failure**:

- Any provider failure makes the run **FAIL** with "the live numbers are not valid". A run that
  escalated tickets only because calls failed says nothing about the model, however good its
  escalation recall looks.
- After three provider failures in a row the run stops, saves nothing and exits with code 4, so a
  dead quota does not burn through the other tickets.
- `--calls-per-minute N` (`make eval CPM=N`) spaces every model call, embeddings included, at
  least `60 / N` seconds apart. Set it below the provider's requests-per-minute limit. A ticket
  takes about 4 to 6 calls (classify, embed, draft, judge, and again on a retry).

## Reading the results

- Rates carry a 95% Wilson interval. With 78 tickets one miss moves a rate noticeably.
- The labeled set is synthetic and written by the developer. Do not read the numbers as a forecast
  for real traffic.
- Retrieval in the report still uses the harness's hashed embeddings, not the live embedder.
- The mock store has no real latency, so seconds per ticket is the model's time, not the system's.
