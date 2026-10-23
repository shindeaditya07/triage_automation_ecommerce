# Correction to this report

As first generated, this report counted the standard fallback template as a model draft, so it
showed "either try 1.000" and "fell back 0.000". The live evaluator (`evals/live.py`) did not
check which model wrote the draft that came back.

The per-ticket records show 11 of the 58 tickets that reached drafting ended on attempt 3. Only 2
attempts are made, so attempt 3 is the fallback template. The corrected figures are:

| Figure | First report | Corrected |
|---|---|---|
| Model draft passed grounding on either try | 1.000 (0.94-1.00) | 0.810 (0.69-0.89), 47 of 58 |
| Fell back to the standard template | 0.000 (0.00-0.06) | 0.190 (0.11-0.31), 11 of 58 |

`report.json` and `report.md` were edited to these values and the 11 tickets relabelled
`fallback_template`. Nothing else changed: no model was called again, and the classification,
escalation, cost and first-try figures are as measured. The evaluator is fixed in the same commit.
