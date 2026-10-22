# Ticket API (owner only)

All routes need a signed-in owner (cookie from `/api/v1/auth/login`); 401 otherwise. Without a
database they answer 503.

## Reading (this branch)

- `GET /api/v1/tickets?status=&intent=&limit=&offset=` is the queue. `status` may repeat
  (`?status=new&status=classified` for an "In progress" tab). `limit` is 1 to 100 (default 50).
  Most urgent first (critical, high, normal, low, then tickets never classified), oldest first
  within an urgency. Each item says whether it has a draft, is suggestion-only, or holds the
  fallback template, so the queue can show the owner-visible notice. Soft-deleted tickets never
  appear.
- `GET /api/v1/tickets/{ticket_id}` is the whole ticket: the customer's original message, the
  classification and flags, the order snapshot the draft was made from, and the latest draft with
  its citations, the full text of each cited policy passage, and the grounding result.
  `needs_original_viewed` is true for escalated, suspected-injection and suggestion-only tickets:
  the screen must make the owner view the original message before it enables Approve.

Nothing here changes a ticket.

## Acting on a draft

- `PATCH /api/v1/tickets/{id}/draft` `{reply_text, override?}` saves the owner's edit as the draft's
  `final_text`; the model's own `reply_text` is never overwritten. The deterministic validator runs
  on the text. With warnings, the save is refused (422, with the failures) unless `override`
  `{reason, overridden_rules}` names every rule that fired; the override is then recorded
  (`draft.edit_override`). An override when nothing fired is refused. The edit and its diff are
  recorded (`draft.edited`).
- `POST /api/v1/tickets/{id}/approve` `{original_viewed, confirm_suggestion_only}` approves the
  saved text (or the model's draft if unedited). The server, not the screen, enforces:
  escalated, suspected-injection and suggestion-only tickets need `original_viewed: true`;
  suggestion-only drafts also need `confirm_suggestion_only: true`; the text is re-checked, and
  warnings need an override on record for exactly those rules (else 409). What is approved is the
  text that was checked: if another tab changed it in between, the approval fails (409). The
  decision, the actor and the edit diff go to the audit log (`ticket.approved`).
- `POST /api/v1/tickets/{id}/reject` `{reason}` (at least 10 characters) records the reason
  (`ticket.rejected`).

A draft is decided once: only a `pending` draft on a `drafted` ticket accepts any of these (409
otherwise).

## Mock send (spec FR-13)

Approving also records the send in a second transaction: the draft gets `sent_at`, the ticket
becomes `sent`, and the audit log gets `ticket.mock_sent` with the draft, the approver and the
length of the final text (the text itself is on the draft as `final_text`). **No message leaves the
system.** If recording the send fails, the ticket stays `approved` (the approval is never lost) and
`POST /api/v1/tickets/{id}/send` finishes it; calling it again, or on a ticket already sent, changes
nothing.
