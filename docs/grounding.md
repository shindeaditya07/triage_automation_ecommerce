# Grounding validator (rules G-1 to G-10)

`services/api/grounding/` checks a drafted reply against the order record, the customer's message
and the policy passages it cites (spec section 13.1). It is a pure function:

```python
validate(reply_text, citations, GroundingContext(...)) -> ValidationResult
```

A `ValidationResult` lists named `RuleFailure`s (rule ID, the offending span, one-line detail, and
`citation_missing` when the value is right but no citation backs it). It never returns a bare
pass or fail. The rules fail closed: a value they cannot place is a failure.

**Status in the pipeline:** the drafting step runs the check on every draft (`docs/pipeline.md`,
steps 4 to 6). A failed draft is retried once with the reasons, then replaced by the fixed
fallback template. The process_ticket handler is still not registered in the app, so nothing runs
this in staging yet. Owner edits are not checked yet (next branch).

## The judge and the combined check

`check_grounding(client, reply, citations, context, passages)` in `check.py` is the single entry
point. It returns a `GroundingResult` (stored with the draft) and the judge call for the cost record.

1. The deterministic validator runs. If it finds anything the draft is blocked and the judge is **not
   asked**: nothing it said could change the outcome, and the call would cost money.
2. If the validator passes, the judge model (`judge.py`, prompt `prompts/judge.md`) reads the reply,
   its citations, the order (without the customer's email) and the retrieved passages. It returns
   `grounded`, a list of issues and a confidence. Any issue blocks the draft. The judge cannot
   approve anything the validator blocked.
3. If the judge cannot answer (timeout, rejected request, unusable output) the draft is blocked with
   `judge_error` set. A draft is never waved through because the check was unavailable.

The judge's confidence is stored but does not block on its own. The prompt tells the judge to treat
doubt as unsupported. A draft blocked only because the judge was down is not retried (a second
draft cannot fix an outage); the ticket escalates as `grounding_unavailable`.

## Retry and fallback

`draft_ticket` asks for at most two drafts. After a failure the second request carries the
failure reasons. If the second also fails, `fallback_template` from the config is used, after the
rules have confirmed it is safe for that ticket. The fallback is stored with `attempt = 3`,
`kind = fallback_template`, `model = fallback-template`, confidence 0 and `proposed_action =
escalate`, and gets a `draft.fallback_used` audit event. The failed attempts are kept as
`draft.attempt_failed` events with their rules and reasons but not their text.

With the all-Gemini setup the drafter and the judge are the same model family
(`JUDGE_SAME_FAMILY_OK=true`), so the judge is a weaker independent check than spec 13.2 intends.

## The rules

| Rule | Fails when | How |
|---|---|---|
| G-1 | An amount, date, order number or tracking number is not in the order, or is in the order but the reply does not cite that field | Extract values from the reply; look each up in the order; a citation of the field (or its parent, e.g. `items`) covers it |
| G-2 | A duration, or an amount said to come from policy, is not in a cited passage | Same extractors run over the cited passages |
| G-3 | The reply promises an outcome or offers compensation | `commitment_patterns` |
| G-4 | A citation names a policy ID that is not loaded, or an order field that is not in the order | Lookup |
| G-5 | A claim the lists recognise has no source: a cause, a handling step, a product property, a courier, a capitalised name | Lists in config; allowed only if the order, the customer's message or a cited passage says it |
| G-6 | Another customer's email or order number; an order that belongs to a different sender | Compared with the sender and the order |
| G-7 | The reply obeys an instruction in the customer's message, or repeats five words of one | `obedience_patterns`, and word runs shared with a sentence that the injection scanner flags |
| G-8 | The last line is not exactly the store sign-off | Exact match. The drafting step adds the sign-off itself and removes a closing the model wrote (`grounding/sign_off.py`); owner edits are checked as typed |
| G-9 | More than one apology | `apology_patterns` |
| G-10 | Longer than `max_reply_chars` | Length |

A G-1 failure for a value that is in the order but not cited says which field to cite (for
example `identifier from the order is not cited; cite orders.order_id`). The drafting step puts
that reason in front of the model on its retry, so the second draft can add the citation.

Which side judges a value: dates and identifiers are order-derived (G-1); durations are always
policy-derived (G-2); an amount is judged by G-2 when its sentence names a policy ID or the word
"policy", or when it appears in a policy passage but not in the order, and by G-1 otherwise.
Ranges ("3 to 5 days", "3-5 business days") compare as ranges. Dates are compared in IST, the way
the order store shows them.

## The config file

`grounding_config.yaml` at the repo root holds every list. Change what counts as a promise, an
apology or an unsupported claim there, with no code change. Set `GROUNDING_CONFIG_PATH` to use
another file. It is validated at load: an unknown key, a missing key or a pattern that is not a valid
regular expression stops startup. The API image copies it next to `policy_constants.yaml`.

## How it is tested

- `tests/grounding/`: each rule has passing and failing cases, plus the extractors, the order index,
  config loading and bad config. CI enforces at least 95% branch coverage on this package.
- `make eval-smoke`: all 40 seeded bad drafts (`evals/datasets/bad_drafts.jsonl`) must be caught,
  each with its expected rule, and none of the 24 good drafts (`good_drafts.jsonl`) may be blocked.
  Either failure fails the run.

## Owner edits

When the owner edits a draft, the same deterministic rules re-run on the new text as a **warning**
(spec 13.4). `POST /api/v1/tickets/{ticket_id}/draft/validate` (owner sign-in and CSRF header
required) takes `{"reply_text": "..."}` and always answers 200 with `{"passed": bool, "failures":
[{rule_id, span, detail, citation_missing}]}`. It never blocks saving. It is checked against the
draft's own order snapshot and citations, the ticket's message and the loaded policy. The judge is
not run on edits. An unknown ticket or one with no draft is 404; with no database the route is 503.

Saving despite a warning needs an `EditOverride`: a reason, the rules being overridden, who and
when (`grounding/edit.py`). `check_override` rejects an override with no reason, one for rules that
did not fire, and one that leaves a firing rule uncovered. `PipelineStore.record_edit_override`
appends it to the audit log as `draft.edit_override` (reason and rules, not the text).

The save and approve routes (docs/ticket-api.md) call `check_override` and
`record_edit_override`'s equivalent in one transaction with the edit, and store the edit diff on
approval.

## What these rules do not catch

Read this before trusting a green result.

- **G-5 is list-based.** It recognises causes, handling steps, product properties, couriers and
  names by the words in the config. A claim phrased in words no list anticipates passes. The
  seeded bad drafts were written first and the lists were tuned after seeing them, so catching all
  40 is a floor, not evidence of coverage. The good drafts are the check against false blocks;
  both sets should grow whenever a real draft is mis-judged.
- **Meaning is checked only by the judge.** "Your refund is on its way" when the order shows no
  refund fails the rules only if a pattern matches. The judge covers meaning, but it is a model: it
  can miss things, it has no seeded corpus yet, and it has not been run against a live model.
- **Numbers written as words** are understood only for 0 to 10, and only for durations.
- **Names** are the capitalised words in the middle of a sentence that no source mentions. A name
  at the start of a sentence is not checked.
- **Wrong language or tone** is not checked.
