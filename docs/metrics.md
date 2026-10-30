# Metrics

`GET /api/v1/metrics?start=YYYY-MM-DD&end=YYYY-MM-DD` (owner only) returns the spec section 18
numbers for a range of IST days. Both dates are optional: the default is the last 30 days ending
today. A range is at most 366 days. The page is `/metrics`, with a 7 day, 30 day and custom range
and a CSV export. The page shows no tile for time to draft or time to send; the API and the CSV
export still carry them.

Everything is computed on request from stored rows (`tickets`, `drafts` and the audit log), so no
figure resets on a restart. Tickets are those *received* in the range.

| Figure | Computed from |
|---|---|
| Tickets by intent and urgency | `tickets.intent`, `tickets.urgency` (`none` = not classified) |
| Draft acceptance | the latest draft of each approved ticket: final text equals the draft after collapsing whitespace, over all approved |
| Time to draft | `tickets.received_at` to the first `ticket.drafted` event (all hours) |
| Time to send | `ticket.drafted` to `ticket.mock_sent`, counting only Monday to Saturday 10:00 to 18:00 IST (public holidays are not excluded) |
| Grounding failures | `rule_ids` of each `draft.attempt_failed` event, per rule G-1 to G-10 |
| Escalation rate | escalated tickets over all; a combined reason such as `legal_threat,prompt_injection` counts once for each |
| Fallback rate | tickets whose latest draft is the fallback template, over tickets with a draft |
| Cost | the `calls` recorded on classify, escalate and draft events: INR and tokens, per ticket over all tickets in the range, and per day |
| Policy coverage | policy ids cited by each ticket's latest draft |

Percentiles are nearest-rank. A rate with nothing to divide is `null` and shows as a dash.
