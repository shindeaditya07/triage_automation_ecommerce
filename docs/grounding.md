# Grounding validator (rules G-1 to G-10)

`services/api/grounding/` checks a drafted reply against the order record, the customer's message
and the policy passages it cites (spec section 13.1). It is a pure function:

```python
validate(reply_text, citations, GroundingContext(...)) -> ValidationResult
```

A `ValidationResult` lists named `RuleFailure`s (rule ID, the offending span, one-line detail, and
`citation_missing` when the value is right but no citation backs it). It never returns a bare
pass or fail. The rules fail closed: a value they cannot place is a failure.

**Status in the pipeline:** the validator exists and is scored by the eval harness, but nothing calls it
yet. A stored draft is still unchecked (`grounding_result` is NULL). Wiring it in, with the retry and
the fallback template, is the next branch.

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
| G-8 | The last line is not exactly the store sign-off | Exact match |
| G-9 | More than one apology | `apology_patterns` |
| G-10 | Longer than `max_reply_chars` | Length |

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

## What these rules do not catch

Read this before trusting a green result.

- **G-5 is list-based.** It recognises causes, handling steps, product properties, couriers and
  names by the words in the config. A claim phrased in words no list anticipates passes. The
  seeded bad drafts were written first and the lists were tuned after seeing them, so catching all
  40 is a floor, not evidence of coverage. The good drafts are the check against false blocks;
  both sets should grow whenever a real draft is mis-judged.
- **Meaning is not checked.** "Your refund is on its way" when the order shows no refund fails only
  if a pattern matches. The judge (a separate model, planned in this phase) covers meaning, and it
  can only add blocks.
- **Numbers written as words** are understood only for 0 to 10, and only for durations.
- **Names** are the capitalised words in the middle of a sentence that no source mentions. A name
  at the start of a sentence is not checked.
- **Wrong language or tone** is not checked.
