import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../api/client";
import { useAudit, useTicket } from "../api/tickets";
import { describeEvent } from "../auditView";

const PAGE = 15;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function AuditPage() {
  const { ticketId } = useParams();
  return (
    <div className="app">
      <header className="topbar">
        <h1>ReplyDesk</h1>
        <Link to="/">← Back to the queue</Link>
      </header>
      <main className="wide">
        <h2 className="ticket-title">Audit log</h2>
        <TicketPicker current={ticketId ?? ""} />
        {ticketId && UUID.test(ticketId) ? (
          <Trail ticketId={ticketId} key={ticketId} />
        ) : (
          <p className="hint">
            {ticketId
              ? "That is not a ticket id."
              : "Paste a ticket id to see everything that happened to it."}
          </p>
        )}
      </main>
    </div>
  );
}

function TicketPicker({ current }: { current: string }) {
  const [value, setValue] = useState(current);
  const navigate = useNavigate();
  function submit(event: FormEvent) {
    event.preventDefault();
    const id = value.trim();
    if (id) navigate(`/audit/${id}`);
  }
  return (
    <form className="picker" onSubmit={submit}>
      <label>
        Ticket id
        <input
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="e.g. 733a3f78-9ece-48fb-873f-f448cb5cf1cf"
          spellCheck={false}
        />
      </label>
      <button type="submit">Show</button>
    </form>
  );
}

function Trail({ ticketId }: { ticketId: string }) {
  const [page, setPage] = useState(0);
  const audit = useAudit(ticketId);
  const ticket = useTicket(ticketId);
  const events = audit.data ?? [];
  const pages = Math.max(Math.ceil(events.length / PAGE), 1);
  const shown = events.slice(page * PAGE, (page + 1) * PAGE);

  if (audit.isPending) {
    return (
      <p className="status" role="status">
        Loading…
      </p>
    );
  }
  if (audit.isError) {
    const missing = audit.error instanceof ApiError && audit.error.status === 404;
    return (
      <p className="status error" role="alert">
        {missing ? "There is no ticket with that id." : "Could not load the audit log."}
      </p>
    );
  }
  return (
    <>
      <p>
        <Link to={`/tickets/${ticketId}`}>{ticket.data?.subject || "Open the ticket"}</Link>
        {" · "}
        {events.length} event{events.length === 1 ? "" : "s"}, oldest first
      </p>
      <ol className="trail" aria-label="Events">
        {shown.map((event) => {
          const view = describeEvent(event);
          return (
            <li key={event.event_id} className={`event tone-${view.tone}`}>
              <div className="event-head">
                <strong>{view.label}</strong>
                <span className="hint">
                  <time dateTime={event.occurred_at}>
                    {new Date(event.occurred_at).toLocaleString()}
                  </time>{" "}
                  · {event.actor}
                </span>
              </div>
              {view.lines.map((line, i) => (
                <p key={i} className="event-line">
                  {line}
                </p>
              ))}
              {view.block && <pre>{view.block}</pre>}
              <details>
                <summary>Raw record</summary>
                <pre>{JSON.stringify(event.payload, null, 2)}</pre>
              </details>
            </li>
          );
        })}
      </ol>
      {pages > 1 && (
        <nav className="pager" aria-label="Pages">
          <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>
            Previous
          </button>
          <span>
            Page {page + 1} of {pages}
          </span>
          <button type="button" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>
            Next
          </button>
        </nav>
      )}
    </>
  );
}
