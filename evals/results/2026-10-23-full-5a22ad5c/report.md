# Eval report 2026-10-23 (full-5a22ad5c, full)

- model: `gemini-3.5-flash-lite (judge gemini-3.5-flash-lite)`, temperature 0.0
- policy version 1.1, policies.md sha256 `fd509177b167`
- 78 labeled tickets, 40 seeded bad drafts
- prompt hashes: {'classify.md': 'b22650666285b4f5cb5fef2638276cd1139e25ad7723a97ecba1a8efe107b139', 'draft.md': '6697d93dfa0184dc9e21dd62230d99a15b627b0900c22929f3ff6db3143fae06', 'judge.md': '9c700a029978388748371a36b84e3af6f1d272047e4c89d45059dc8fe2f4de34'}
- grounding validator: evaluated
- result: **FAIL**
- corrected after the run: see CORRECTION.md (the fallback rate was first reported as 0)

- FAILURE: live (gemini-3.5-flash-lite): escalation recall 0.8928571428571429 is below 0.95
## Comparison with the keyword baseline

| Metric | live (gemini-3.5-flash-lite) | keyword-baseline |
|---|---|---|
| Intent accuracy | 0.897 (0.81-0.95) n=78 | 0.795 (0.69-0.87) n=78 |
| Urgency accuracy | 0.821 (0.72-0.89) n=78 | 0.603 (0.49-0.70) n=78 |
| Escalation recall | 0.893 (0.73-0.96) n=28 | 0.500 (0.33-0.67) n=28 |
| Escalation precision | 0.926 (0.77-0.98) n=27 | 0.737 (0.51-0.88) n=19 |
| No-draft escalation recall | 0.905 (0.71-0.97) n=21 | 0.667 (0.45-0.83) n=21 |
| Outcome accuracy | 0.936 (0.86-0.97) n=78 | 0.756 (0.65-0.84) n=78 |

## live (gemini-3.5-flash-lite)

| Metric | Value (95% Wilson) |
|---|---|
| Intent accuracy | 0.897 (0.81-0.95) n=78 |
| Urgency accuracy | 0.821 (0.72-0.89) n=78 |
| Escalation recall | 0.893 (0.73-0.96) n=28 |
| Escalation precision | 0.926 (0.77-0.98) n=27 |
| No-draft escalation recall | 0.905 (0.71-0.97) n=21 |
| Outcome accuracy | 0.936 (0.86-0.97) n=78 |

Missed escalations: T029, T052, T068. False escalations: T050, T067.

| Intent | Support | Precision | Recall |
|---|---|---|---|
| order_status | 25 | 1.000 (0.86-1.00) n=23 | 0.920 (0.75-0.98) n=25 |
| refund | 15 | 0.933 (0.70-0.99) n=15 | 0.933 (0.70-0.99) n=15 |
| return | 6 | 0.833 (0.44-0.97) n=6 | 0.833 (0.44-0.97) n=6 |
| damaged_item | 7 | 0.778 (0.45-0.94) n=9 | 1.000 (0.65-1.00) n=7 |
| product_question | 8 | 0.875 (0.53-0.98) n=8 | 0.875 (0.53-0.98) n=8 |
| cancellation | 4 | 1.000 (0.51-1.00) n=4 | 1.000 (0.51-1.00) n=4 |
| address_change | 2 | 1.000 (0.34-1.00) n=2 | 1.000 (0.34-1.00) n=2 |
| payment | 5 | 0.800 (0.38-0.96) n=5 | 0.800 (0.38-0.96) n=5 |
| warranty | 2 | 1.000 (0.21-1.00) n=1 | 0.500 (0.09-0.91) n=2 |
| other | 4 | 0.600 (0.23-0.88) n=5 | 0.750 (0.30-0.95) n=4 |

## keyword-baseline (comparison only, does not gate the run)

| Metric | Value (95% Wilson) |
|---|---|
| Intent accuracy | 0.795 (0.69-0.87) n=78 |
| Urgency accuracy | 0.603 (0.49-0.70) n=78 |
| Escalation recall | 0.500 (0.33-0.67) n=28 |
| Escalation precision | 0.737 (0.51-0.88) n=19 |
| No-draft escalation recall | 0.667 (0.45-0.83) n=21 |
| Outcome accuracy | 0.756 (0.65-0.84) n=78 |

Missed escalations: T002, T010, T011, T016, T018, T038, T039, T040, T041, T052, T064, T073, T074, T075. False escalations: T027, T062, T065, T067, T077.

| Intent | Support | Precision | Recall |
|---|---|---|---|
| order_status | 25 | 1.000 (0.81-1.00) n=16 | 0.640 (0.45-0.80) n=25 |
| refund | 15 | 0.933 (0.70-0.99) n=15 | 0.933 (0.70-0.99) n=15 |
| return | 6 | 0.833 (0.44-0.97) n=6 | 0.833 (0.44-0.97) n=6 |
| damaged_item | 7 | 1.000 (0.61-1.00) n=6 | 0.857 (0.49-0.97) n=7 |
| product_question | 8 | 1.000 (0.65-1.00) n=7 | 0.875 (0.53-0.98) n=8 |
| cancellation | 4 | 0.750 (0.30-0.95) n=4 | 0.750 (0.30-0.95) n=4 |
| address_change | 2 | 1.000 (0.34-1.00) n=2 | 1.000 (0.34-1.00) n=2 |
| payment | 5 | 0.800 (0.38-0.96) n=5 | 0.800 (0.38-0.96) n=5 |
| warranty | 2 | 1.000 (0.21-1.00) n=1 | 0.500 (0.09-0.91) n=2 |
| other | 4 | 0.250 (0.10-0.49) n=16 | 1.000 (0.51-1.00) n=4 |

## Live run

- drafter `gemini-3.5-flash-lite`, judge `gemini-3.5-flash-lite`
- tickets that reached drafting: 58
- model draft passed grounding on the first try: 0.552 (0.42-0.67) n=58
- model draft passed grounding on either try: 0.810 (0.69-0.89) n=58
- fell back to the standard template: 0.190 (0.11-0.31) n=58
- drafting escalated the ticket instead: 0.000 (0.00-0.06) n=58
- grounding failures by rule (every failed attempt): G-1 x2, G-3 x7, G-5 x3, G-8 x29
- classification failed, escalated: none
- provider failures (the call failed, not the model; any makes the run INVALID): none
- cost: Rs 17.77 in total (Rs 0.00 to embed the policy), Rs 0.228 per ticket
- seconds per ticket: p50 24.11, p95 33.41

## Retrieval: evaluated

Embeddings in this run are the fake client's hashed bag-of-words vectors: the intent-required passages are a real measurement, the vector search is a harness check.

- tickets measured: 56 (drafted or suggestion-only, with cited passages)
- cited passages retrieved: 0.933 (0.87-0.97) n=105
- of those, by intent alone: 0.810 (0.72-0.87) n=105
- tickets with every cited passage: 0.875 (0.76-0.94) n=56
- mean passages per ticket: 9.2
- tickets missing a cited passage: T007, T013, T020, T030, T046, T070, T073

## Grounding: evaluated

40 of 40 bad drafts caught; 0 of 24 good drafts wrongly blocked

- caught: 1.000 (0.91-1.00) n=40
- right rule reported: 1.000 (0.91-1.00) n=40
- good drafts wrongly blocked: none
