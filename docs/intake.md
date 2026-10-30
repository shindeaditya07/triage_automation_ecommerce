# Ticket intake

Two endpoints turn a customer message into a ticket and queue it for processing.

| Endpoint | Who calls it | Authentication |
|---|---|---|
| `POST /api/v1/tickets/form` | the contact form on the store's site | none (public); rate limited, honeypot |
| `POST /api/v1/tickets/email` | whatever forwards inbound email | HMAC-SHA256 signature of the body |

Both accept `application/json` only, and both write the same thing: a ticket (status `new`), an
`audit_log` entry (`ticket.ingested`) and a `process_ticket` job, in **one transaction**. A ticket
never exists without its job.

## The form

```json
{
  "name": "Asha",
  "email": "asha@example.com",
  "subject": "Where is my order?",
  "message": "Order KHG-10001 has not arrived.",
  "order_id": "KHG-10001",
  "attachments": [{"filename": "box.jpg", "content_type": "image/jpeg", "size_bytes": 220114}],
  "submission_id": "6f1c0f0e-1f0e-4a49-8f65-9a4b2c5f3b3d",
  "website": ""
}
```

* `website` is the honeypot. The page hides it; people leave it empty, bots fill it in.
* `submission_id` is optional. Generate one per page load and a double click or a retry becomes one
  ticket instead of two.
* Only metadata is accepted for attachments (name, type, size). Image contents are not read in v1.

Every request that gets past the checks below answers `202 {"status": "received"}`, whether it made
a ticket, repeated an earlier one or was caught by the honeypot. The response tells a caller nothing
about what the server did. Refusals are `429` (rate limit, with `Retry-After`), `413` (too large),
`415` (not JSON) and `422` (invalid; the reply names the fields, never echoes the input).

## The customer contact page

`/contact` on the web app is a public page (no sign-in) that posts to the form endpoint above. A
customer gives an email address and a message, and can add a name, a subject and an **order number**.
The order number travels as `order_id`; the pipeline looks the order up and checks the customer's
address against it, so nothing about the order is shown on the page. The page generates one
`submission_id` per visit (a retry after an error is still one ticket), hides the honeypot field
from people and screen readers, and answers every refusal in plain words (429, 413, 422, 503).
Attachments are not offered, because only their metadata would be stored.

The same form is also a **New complaint** button on the owner queue (a dialog), for a complaint that
arrives by phone or chat. It posts through the same public endpoint, so the ticket looks like any
other `form` ticket: the audit log does not record that the owner typed it, and it counts against the
same per-address rate limit (3 a minute from one network).

Because the form endpoint is rate limited per address (3 a minute, 10 an hour), a store that wants to
push many customers' messages from its own server should use the signed email endpoint below, which
is not rate limited per address. A browser page on a *different* site cannot call the form endpoint
yet: the API has no CORS rules, so link to `/contact` instead. Step-by-step: the "customer contact"
guide.

## The email endpoint

```json
{
  "message_id": "<abc123@mail.example>",
  "received_at": "2026-10-19T09:30:00Z",
  "customer": {"email": "asha@example.com", "name": "Asha"},
  "subject": "Re: Where is my order?",
  "body": "Any update?",
  "in_reply_to": "<first-message@mail.example>",
  "order_id_hint": "KHG-10001",
  "attachments": []
}
```

Sign the **exact bytes** you send and put the hex digest in `X-Signature`:

```python
import hashlib, hmac, json, requests

body = json.dumps(payload).encode()
signature = "sha256=" + hmac.new(HMAC_SECRET.encode(), body, hashlib.sha256).hexdigest()
requests.post(f"{API}/api/v1/tickets/email", data=body,
              headers={"Content-Type": "application/json", "X-Signature": signature})
```

`HMAC_SECRET` is the same value as in the API's environment. Replies: `201` with
`{"ticket_id", "thread_id", "created": true}` for a new message, `200` with `"created": false` for one
already received, `401` for a missing or wrong signature (the same answer for both), `503` while
no `HMAC_SECRET` is configured.

The signature covers the body only, so a captured request can be sent again. That is harmless for
a message already received (it is recognised and ignored) but it is the reason to keep the secret
private and the endpoint on HTTPS.

## What happens to the text

Before anything is stored, and so before any model sees it:

1. Normalised to Unicode NFKC (full-width look-alikes become plain characters).
2. HTML removed: tags, comments, `<script>`/`<style>`, and the content of elements hidden from view
   (`display:none`, `hidden`, zero-size text, text pushed off-screen). Text that was only
   in a hidden element is dropped, and the audit entry records that this happened.
3. Links replaced with `[link removed]`; invisible and control characters removed.
4. PII masked (below).

Prompt-injection text such as "ignore your instructions" is plain words, so it is **kept** as data and
left for the escalation stage to flag. What cannot survive is markup: a message cannot close a
`</customer_message>` block or open a `<system>` one.

| Masked | Rule |
|---|---|
| Card numbers | Luhn-valid 13 to 19 digits with a plausible length for the first digits, and any 4-4-4-4 or 4-6-5 grouping, even if the check digit is wrong. Never stored. |
| Aadhaar | 12 digits grouped 4-4-4, or 12 unbroken digits with a valid Verhoeff check digit |
| PAN | `AAAPA1234A` shape with a valid holder-type letter |
| UPI IDs | `name@handle` where the handle has no dot |
| Bank accounts | 9 to 18 digits directly after "account", "acct" or "a/c" |

Tracking numbers, order numbers, phone numbers and email addresses are left alone, because masking
them would stop the reply being useful. A bank account number written without the word "account" near it will not
be caught; that is the cost of not masking every long number.

The original text is not kept. `audit_log` holds counts and flags only (`pii_masked`,
`links_removed`, `html_found`, `hidden_text_removed`), never the text.

## Threading and duplicates

* A message is identified by `external_message_id` (the email's `message_id`, or `form-<submission_id>`).
  Receiving it again returns the first ticket and changes nothing.
* A message with `in_reply_to` joins the thread of that earlier message **only if it came from the same
  customer address**. Otherwise it starts a new thread, so a stranger cannot attach to someone else's
  conversation. Each message is still its own ticket; they share `thread_id`.

## Settings

| Variable | Default | |
|---|---|---|
| `HMAC_SECRET` | none | signs the email endpoint; 32+ characters outside local |
| `FORM_MAX_BODY_BYTES` / `FORM_MESSAGE_MAX_CHARS` | 20000 / 5000 | form request and message size |
| `EMAIL_MAX_BODY_BYTES` / `EMAIL_BODY_MAX_CHARS` | 262144 / 20000 | email request and body size |
| `FORM_IP_MAX_PER_MINUTE` / `FORM_IP_MAX_PER_HOUR` | 3 / 10 | per client address |
| `ATTACHMENT_MAX_COUNT` / `ATTACHMENT_MAX_BYTES` | 5 / 10485760 | declared attachment limits |
| `TRUSTED_PROXY_HOPS` | 0 | set to 1 on Render so the limit sees the visitor, not the proxy |

## Known limits

* The rate limiter is in memory, per process. Someone rotating addresses is not slowed, and each
  accepted message costs a model call. A global daily cap is not built yet.
* The sender's address is taken on trust from the form. Replies go to that address and nowhere
  else, but anyone can type anyone's address into the form.
* Attachments are metadata only, so there is nothing to scan.
