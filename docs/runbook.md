# Runbook

What can go wrong, how it shows up, how to check, and how to fix it. Written for the owner of a
single small deployment. Commands that touch a database need the database's **external** URL;
keep it out of chat, tickets and commits.

## The two health checks

| URL | Meaning | Answers |
|---|---|---|
| `/healthz` | The process is up. Never touches the database. | `200` |
| `/readyz` | This instance can do useful work now. | `200 {"status":"ok"}`, or `503` naming what failed |

`/readyz` checks four things and names each failing one: `db`, `pgvector`, `migrations`,
`policy_index`. If the database is down the other three are listed as failing too, with "not
checked". Deploys wait on `/healthz`, not `/readyz`, so a degraded instance still deploys.

## Quick triage

| You see | Most likely | Go to |
|---|---|---|
| `/readyz` is 503, `policy_index` only | Policy index never built, or `policies.md` changed | [Policy index](#policy-index-missing-or-stale) |
| `/readyz` is 503, `db` | Database down or `DATABASE_URL` wrong | [Database](#database-unreachable) |
| `/readyz` is 503, `migrations` | Code is ahead of the database | [Migrations](#migrations-behind) |
| Tickets arrive but none are drafted | Pipeline off (no provider) | [Tickets not drafted](#tickets-are-received-but-not-drafted) |
| Tickets stuck in `new` or `classified` | Worker or store trouble | [Stuck tickets](#tickets-stuck-in-new-or-classified) |
| Tickets in `failed` | A job ran out of tries | [Failed tickets](#failed-tickets) |
| Many `escalated`, reason starts `llm_error` | The model provider is failing | [Model provider](#model-provider-failing) |
| Cannot log in | Lockout, wrong hash, or cookies | [Login](#cannot-log-in) |
| Web page blank or says "waking up" | Sleeping free-tier service, or CSP | [Web app](#web-app-problems) |
| Metrics cost is zero | Price settings empty | [Metrics](#metrics-look-wrong) |

## Where to look

- **API logs** (Render: the API service, Logs). Lines worth searching for:
  - `ticket received ticket_id=... channel=... created=...`: intake worked. `created=False` is a
    duplicate that was ignored.
  - `job ... failed (attempt N)`: a try failed; it will be retried until `max_attempts` (3).
  - `job sweep: N re-queued, M gave up`: the sweeper found jobs whose worker vanished.
  - `pipeline disabled: ...`: drafting is off and the line says why.
  - `intake disabled: invalid settings [...]`: intake settings are wrong; the names are listed.
- **The ticket's audit log** (web app, Audit log, or `/audit/<ticket id>`). It records each step,
  and why a ticket escalated or failed.
- **The ticket row**: `status`, `escalation_reason`, `failure_reason`.

## Failure modes

### Database unreachable

- **Shows as:** `/readyz` 503 with `db` failing; the web app shows errors; `/healthz` is still 200.
- **Check:** on Render, is the Postgres instance running? Is `DATABASE_URL` on the API service the
  **internal** URL, in the form `postgresql+asyncpg://...`?
- **Fix:** start the database or correct `DATABASE_URL`. The API recovers by itself, with no
  restart, as soon as the database answers. Jobs in flight are picked up again by the sweeper.

### Migrations behind

- **Shows as:** `/readyz` 503 with `migrations` failing, the detail naming the database and code
  revisions.
- **Fix:** run `make migrate` against the database (`DATABASE_URL` set to the external URL). Run
  it after every deploy that adds a migration. It is safe to run twice.

### Policy index missing or stale

- **Shows as:** `/readyz` 503 with only `policy_index` failing. Drafting needs the index, so expect
  drafts to be missing or tickets to escalate until it is built.
- **Cause:** the index is built by hand, not at startup. It is missing on a new database, and
  stale after `docs/policies.md` changes.
- **Fix:** with `DATABASE_URL` and the embedding key set, run `make index-policies`. Then check
  `/readyz`. `make policy-check` must pass whenever `docs/policies.md` changes.

### Tickets are received but not drafted

- **Shows as:** tickets appear and stay `new`; the log has `pipeline disabled: LLM_PROVIDER is not
  set`.
- **Cause:** `LLM_PROVIDER` is empty or `fake`, or a model setting is invalid.
- **Fix:** set the model variables (see `.env.example`), save, and **redeploy** (see
  [Render notes](#render-notes)). Jobs already queued are processed once the pipeline is on.

### Tickets stuck in `new` or `classified`

- **Check:** the ticket's audit log shows the last step that finished.
- **Order store down:** the lookup step fails the job; it is retried up to 3 times with backoff.
  Check `MOCK_STORE_URL` and that the mock-store service is running. After it recovers, the next
  retry continues from the lookup without classifying again.
- **Worker gone:** a job locked by a process that died is re-queued by the sweeper after
  `LOCK_TIMEOUT_SECONDS` (60), checked every `SWEEP_INTERVAL_SECONDS` (30). Waiting about two
  minutes is usually enough. `JOB_WORKER_ENABLED=false` turns the worker off on purpose.
- Re-running a step never creates a second draft, snapshot or audit entry.

### Failed tickets

- **Shows as:** status `failed`, with a `failure_reason`. This means the job used all three
  tries, or hit an error retrying cannot fix (for example, the order store answered outside its
  contract).
- **Fix:** read the reason, fix the cause (for example, wake the order store), then open the
  ticket and press **Retry processing**. The ticket goes back to the stage it had reached
  (new, classified or drafted), its job gets three fresh tries, and the audit log records
  `ticket.retried` with who pressed it. If it fails again, the reason shows again.

### Model provider failing

- **Shows as:** tickets escalated with `escalation_reason` of `llm_error:<ClassName>`
  (for example `LLMRequestError`, `CircuitOpenError`); the ticket's error text names the provider
  response, such as `HTTP 404 (NOT_FOUND)`.
- **What the system does:** retries 429, 5xx and timeouts up to `LLM_MAX_ATTEMPTS`; after
  `LLM_BREAKER_FAILURES` failures in a row it stops calling for `LLM_BREAKER_COOLDOWN_SECONDS`
  and escalates fast. A model failure never produces a draft.
- **Fix:** a `4xx` other than 429 is a configuration problem: a wrong model name, a bad or
  expired key, or no quota. Correct it and redeploy. Escalated tickets are not re-drafted; the
  owner handles them or the customer re-sends.
- **A judge outage** discards the draft and escalates. It does not let an unchecked draft through.

### Cannot log in

- **Locked out:** after `LOGIN_MAX_FAILURES` (5) wrong passwords the account locks for a time
  that grows to at most `LOGIN_LOCKOUT_MAX_SECONDS` (1 hour). The response is `429` with
  `Retry-After`. Wait it out. Lockout can be triggered by anyone who knows the owner's email;
  this is a known limit.
- **Always 401:** `OWNER_PASSWORD_HASH` is wrong. Regenerate it with `make owner-hash`, put it in
  the environment unquoted by the platform, and redeploy.
- **Session drops:** access tokens last 15 minutes and refresh on their own; two tabs refreshing
  at the same instant can sign you out. Sign in again.

### Web app problems

- **Blank page or red "Content Security Policy" console errors:** the header set on the Render
  Static Site differs from `apps/web/nginx.conf`. Re-enter the values from
  `docs/security-headers-and-a11y.md`, or delete the CSP row to go back to no policy.
- **Old screens after a deploy:** the Static Site does not redeploy by itself. Use Manual Deploy.
- **API calls fail with 404:** the `/api/*` rewrite on the Static Site is missing or points at the
  wrong API address.
- **First request is slow:** free-tier services sleep when idle. The web app shows "server waking
  up" after 8 seconds. Use a paid plan for anything you demo.

### Metrics look wrong

- **Cost is zero:** the `*_PRICE_INR_PER_1M` settings default to 0. Fill them in from the
  provider's price page.
- **Numbers disagree with the queue:** metrics use IST days and the range you pick (default last
  30 days, at most 366). See `docs/metrics.md` for each definition.

## Render notes

- **Saving an environment variable redeploys the image that is already stored**, which may be an
  older build than the latest `develop`. After any env change, re-run the `Staging Deploy`
  workflow (Actions, Run workflow) and confirm Render's Events show a "Deploy hook" deploy tagged
  `develop-<commit>` for the commit you expect.
- The Static Site is separate: it needs its own manual redeploy for web changes, and its headers
  can only be set in the dashboard.
- Never paste a database URL or API key into chat or a ticket. If something fails, copy only the
  error line.

## Release smoke test

`scripts/synthetic_check.py` checks a deployed API after a release: `/readyz` is 200, a signed
email-webhook ticket is accepted (201), and sending the same message again is a duplicate (200).

```bash
SYNTHETIC_CHECK_HMAC_SECRET=<the API's HMAC_SECRET> uv run python -m scripts.synthetic_check --api-url <API URL>
```

It exits 1 and prints `FAIL: ...` if anything is wrong; a 401 on intake means the secret differs
from the API's `HMAC_SECRET`. It does not wait for a draft, because reading a ticket needs the
owner's login. The ticket it creates is real: reject it in the queue (subject starts
`[synthetic check]`). Run it by hand against staging after a release; the release workflow does not deploy or run it.

## Restarts and shutdown

The API runs as one process. On SIGTERM (a deploy or restart) it closes the database pool. A job that was mid-run is re-queued by the sweeper, and every step is safe to
repeat, so a restart loses no ticket and makes no duplicate draft. The automated version of this
check (`make resilience`) is not written yet; until it is, this behavior is covered by unit and
database tests but has not been tried against a killed container.

## Monitoring

None is set up. Staging is free and sleeps, and pinging it keeps it awake and uses free hours, so a monitor is only worth it if you add a paid, always-on environment. If you do:

1. Create an external uptime monitor (UptimeRobot or Better Uptime) on `<API URL>/readyz`, every
   5 minutes, with an email alert. Alert on `readyz`, not `healthz`: only `readyz` notices a lost
   database or a missing policy index.
2. Add a second check on the web address, so a dead Static Site is noticed.
3. Once a week, open the Metrics page and look at the fallback rate and grounding failures. A
   rise means a prompt, a policy or a model changed.

## Data and retention

`RETENTION_DAYS` (default 90) is enforced by a daily `retention_sweep` job. The API queues one per
UTC day (checked hourly, so a restart or outage catches up) and its worker runs it. For every
ticket received before the cutoff it overwrites the customer's address, name, subject, message,
attachment list, order data and all draft text, sets `deleted_at`, and writes a
`ticket.retention_erased` audit entry; each run also writes a `retention.sweep` entry with the
count. The ticket row stays because the audit log is append-only and points at it. It erases by age
whatever the ticket's status, and erased tickets disappear from the ticket list and the metrics.
Customer text is stored after masking card, bank, UPI and Aadhaar numbers; the original text is
not kept. To see whether it is running, look for `retention.sweep` in a ticket-less audit query or
`retention sweep erased` in the API log. Ask before deleting production data by hand.

### When a customer asks to be erased

Sign in as the owner and use **Erase this customer's data** at the bottom of any of their tickets
(you type their address to confirm), or call `DELETE /api/v1/customers/{email}` with the usual
session and CSRF headers. Every live ticket from that address, matched case-insensitively, is
erased exactly as the retention sweep does it, in one transaction, whatever its status. Each ticket
gets a `ticket.customer_erased` audit entry and the request gets one `customer.erasure` entry
holding only the count; the address itself is never written to the audit log. The answer is
`{"erased": n}`, or 404 when there is nothing from that address (mistyped, or already erased). It
cannot be undone, and it does not touch database backups, so a restore would bring the text back.
