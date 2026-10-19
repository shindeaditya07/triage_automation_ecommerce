# Urgency rubric

Urgency answers one question: **how soon does a person need to look at this ticket?** It is not
the same as sentiment (how the customer feels) or escalation (whether the owner must decide).
Label from the customer's message and the facts it implies, not from the order data.

Pick the **highest** level whose test is met.

| Level | Test | Typical examples |
|---|---|---|
| `critical` | Waiting causes harm that cannot be undone, or the message carries legal or safety weight. | A legal, consumer-court or chargeback threat. A product that sparked, smoked or injured someone. Money taken with no order. |
| `high` | The customer is stuck now, a deadline is close, or the store may be at fault and the clock is running. | Damage within the 48-hour report window. A cancellation request on an order that has not shipped. A wrong address on an order about to ship. A parcel two weeks late. A repeat complaint. |
| `normal` | A real request with no deadline. Most tickets. | Where is my order. How do I return this. When will my refund arrive. |
| `low` | Nothing is waiting on the answer. | A general product or policy question, an invoice copy, a thank-you with a small question, a business enquiry. |

## Rules for edge cases

1. **A deadline the customer states counts only if it is credible.** "Urgent!!!" with nothing at
   stake is `normal`. "Needed for a wedding tomorrow" on an order still in transit is `high`.
2. **Angry is not urgent by itself.** Anger raises `sentiment`. It raises urgency only when one of
   the tests above is also met. An angry message about a policy question stays `low` or `normal`.
3. **A window that is closing makes a ticket `high`.** Damage reports (48 hours), cancellations
   before dispatch, and address changes before dispatch all lose their option when time passes.
4. **Windows that have already closed do not raise urgency.** A return request after the 7-day
   window is `normal`: nothing is lost by waiting for the owner's decision.
5. **Safety beats everything.** Any mention of fire, sparks, burning smell, shock, injury or a
   child's safety is `critical`, whatever else the message says.
6. **Several issues in one message:** label the most urgent one.
7. **Vague or non-English messages:** label what can be read. If nothing can be, use `normal`; the
   escalation labels, not urgency, carry the "needs a person" signal.

## How labels are made

- Every label in `datasets/labeled.jsonl` is written or reviewed by a human. The model being
  evaluated never writes its own labels.
- A label the author was unsure about carries a `needs_review` note. A reviewer decides it, then
  clears the note.
- When this rubric changes, relabel affected tickets and say so in the eval report.
