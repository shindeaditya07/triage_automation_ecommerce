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

## Acting on a draft (next branch)

Saving an edit, approving and rejecting are not built yet. When they are, saving calls
`check_override` and `record_edit_override` (docs/grounding.md), and approval stores the edit diff.
