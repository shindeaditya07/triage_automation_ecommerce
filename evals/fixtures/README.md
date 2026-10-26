# Recorded predictions

`recorded_predictions.jsonl` holds one prediction per labeled ticket. `make eval-smoke` replays it
through `FakeLLMClient` so CI never calls a model.

**These are synthetic.** They are not the output of any real model. They agree with the labels except
for a few deliberate mistakes (wrong intents, wrong urgencies, one missed escalation, one false
escalation) so every metric in the report is exercised. Treat smoke numbers as proof that the harness
works, never as a measure of model quality. Real numbers come from `make eval` once a live client
exists (feature/p2-llm-client), and are committed to `evals/results/` with model and prompt hashes.

Each row: `ticket_id`, `classification` (what `FakeLLMClient.classify` returns), and the routing
`outcome` with `escalation_reasons`.
