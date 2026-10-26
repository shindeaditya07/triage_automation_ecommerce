# 0002. Background jobs live in Postgres

- Status: accepted
- Date: 2026-10-19

## Context

Processing a ticket takes several steps, some of them calls to an LLM, and must not be lost when
the API restarts or the host sleeps. FastAPI's `BackgroundTasks` run in the request's process and
vanish with it. A separate queue service (Redis, SQS) would be one more thing to run, pay for and
keep consistent with the database for a single-store demo.

## Decision

Jobs are rows in the `jobs` table. A worker running inside the API process claims one at a time
with `SELECT ... FOR UPDATE SKIP LOCKED`, so any number of workers or API instances can run
without taking the same job and without waiting for each other.

- **States:** `queued` and `failed` (waiting to run again) are claimable once `run_at` has passed;
  `running` holds a lock; `succeeded` and `dead` are final. `dead` always carries `last_error`.
- **Retries:** a handler that raises is retried after 15 s, 30 s, 60 s and so on, up to 10 minutes,
  until `max_attempts` (default 3). `PermanentJobError` skips the retries. A job with no handler is
  dead at once.
- **Giving up is visible:** when a job goes dead its ticket is set to `failed` with a reason
  (`process_ticket gave up after 3 attempt(s): ...`), unless the ticket has already moved past
  `drafted`. Nothing stalls silently.
- **Recovery:** a job `running` for longer than `LOCK_TIMEOUT_SECONDS` is presumed lost. A sweep at
  startup and then every `SWEEP_INTERVAL_SECONDS` puts it back in the queue, or marks it dead if it
  has no attempts left. The sweep is one `UPDATE`, so several instances can sweep together.
- **Idempotency:** `dedupe_key` (`job_type:ticket_id:input_hash`) is unique. Enqueuing the same
  work again returns the existing job, whatever state it is in, so a retried request or a
  re-delivered email creates no second job. Handlers must also be safe to run twice, because the
  sweeper can hand a slow job to a second worker; the first worker's late result is discarded
  (`succeed` and `fail` only apply to the claim the worker made).
- **Where it runs:** the worker and sweeper start with the API (`JobRunner`, from the app's
  lifespan) and stop with it, giving the job in progress ten seconds to finish.
  `JOB_WORKER_ENABLED=false` runs the API without them.

## Consequences

- No new infrastructure. Enqueueing a job can share a transaction with the row that needs it.
- Workers poll (`JOB_POLL_INTERVAL_SECONDS`, 2 s), so a new job waits up to that long. Fine for
  email triage; `LISTEN/NOTIFY` is the upgrade if it ever matters.
- A handler running longer than the lock timeout will be run a second time. Keep the timeout well
  above the longest LLM call (30 s timeout against a 60 s lock today).
- The worker shares the API's event loop and pool, so a handler must never block (ADR 0001).
- There is no command yet to re-queue a dead job. It is a one-line `UPDATE` by hand until the
  owner screens need it.
