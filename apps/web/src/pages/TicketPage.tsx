import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { useAudit, useRetry, useTicket, type TicketDetail } from "../api/tickets";
import { DraftEditor } from "../components/DraftEditor";
import { ActivityList, EvidencePanel } from "../components/EvidencePanel";
import { OwnerReply } from "../components/OwnerReply";
import { ApproveDialog, EraseCustomerDialog } from "../components/ReviewDialogs";
import { complaintTitle, humanize } from "../format";
import { useTitle } from "../hooks/useTitle";
import { errorMessage } from "../ticketView";
import { queueAddress, tabForStatus } from "./queueTabs";

type Dialog = "approve" | "erase" | null;

export function TicketPage() {
  const { ticketId = "" } = useParams();
  const ticket = useTicket(ticketId);
  const audit = useAudit(ticketId);
  useTitle(ticket.data ? complaintTitle(ticket.data.complaint_id, ticket.data.subject) : "Ticket");
  // Back goes to the queue tab the ticket was opened from. A direct link or a refresh has no
  // such memory, so it falls back to the tab the ticket's status belongs to.
  const from = (useLocation().state as { from?: unknown } | null)?.from;
  const back =
    typeof from === "string" && from.startsWith("/tickets")
      ? from
      : ticket.data
        ? queueAddress(tabForStatus(ticket.data.status)?.id ?? "all")
        : "/tickets";
  return (
    <div className="app">
      <header className="topbar">
        <h1>ReplyDesk</h1>
        <Link to={back}>← Back to the queue</Link>
      </header>
      <main id="main" tabIndex={-1} className="wide">
        {ticket.isPending && (
          <p className="status" role="status">
            Loading…
          </p>
        )}
        {ticket.isError && (
          <p className="status error" role="alert">
            Could not load this ticket.{" "}
            <button type="button" onClick={() => void ticket.refetch()}>
              Retry
            </button>
          </p>
        )}
        {ticket.data && <TicketView ticket={ticket.data} events={audit.data ?? []} />}
      </main>
    </div>
  );
}

function typingIn(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  );
}

function RetryButton({ ticketId }: { ticketId: string }) {
  const retry = useRetry(ticketId);
  return (
    <p>
      <button type="button" disabled={retry.isPending} onClick={() => retry.mutate()}>
        {retry.isPending ? "Retrying…" : "Retry processing"}
      </button>
      {retry.isError && (
        <span className="error" role="alert">
          {" "}
          {errorMessage(retry.error)}
        </span>
      )}
    </p>
  );
}

function TicketView({
  ticket,
  events,
}: {
  ticket: TicketDetail;
  events: React.ComponentProps<typeof ActivityList>["events"];
}) {
  const gated = ticket.needs_original_viewed;
  const [viewed, setViewed] = useState(!gated);
  const [editing, setEditing] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const navigate = useNavigate();
  const draft = ticket.draft;
  const actionable =
    draft !== null && draft.review_status === "pending" && ticket.status === "drafted";

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!actionable || dialog || editing || typingIn(event.target)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "a") setDialog("approve");
      if (key === "e") {
        event.preventDefault();
        setEditing(true);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [actionable, dialog, editing]);

  const shownText = draft ? (draft.final_text ?? draft.reply_text) : "";
  return (
    <>
      <h2 className="ticket-title">{complaintTitle(ticket.complaint_id, ticket.subject)}</h2>
      <p className="ticket-from">
        {ticket.customer_name ? `${ticket.customer_name} · ` : ""}
        {ticket.customer_email} · {humanize(ticket.channel)} ·{" "}
        <time dateTime={ticket.received_at}>{new Date(ticket.received_at).toLocaleString()}</time>{" "}
        <span className="badge muted">{humanize(ticket.status)}</span>
      </p>
      {ticket.escalation_reason && ticket.status === "escalated" && (
        <p className="banner danger" role="alert">
          Escalated: {humanize(ticket.escalation_reason)}
        </p>
      )}
      {ticket.escalation_reason && ticket.status !== "escalated" && (
        <p className="banner caution">Flagged: {humanize(ticket.escalation_reason)}</p>
      )}
      {ticket.failure_reason && (
        <p className="banner danger" role="alert">
          Processing failed: {ticket.failure_reason}
        </p>
      )}
      {ticket.status === "failed" && <RetryButton ticketId={ticket.ticket_id} />}

      <div className="detail-grid">
        <section aria-label="Customer message" className="panel">
          <h2>Customer message</h2>
          {viewed ? (
            <p className="original">{ticket.body}</p>
          ) : (
            <>
              <p className="warn-text">
                This message looks risky, so it is hidden until you choose to read it.
              </p>
              <button type="button" onClick={() => setViewed(true)}>
                Show original message
              </button>
            </>
          )}
        </section>

        <section aria-label="Draft reply" className="panel">
          <h2>Draft reply</h2>
          {!draft && ticket.status !== "escalated" && (
            <p className="hint">There is no draft for this ticket.</p>
          )}
          {!draft && ticket.status === "escalated" && (
            <OwnerReply ticketId={ticket.ticket_id} originalViewed={viewed} />
          )}
          {draft?.kind === "owner" && (
            <p className="banner caution">You wrote this reply. No AI draft was involved.</p>
          )}
          {draft?.kind === "fallback_template" && (
            <p className="banner caution">
              This is the standard fallback reply. The AI draft failed its checks.
            </p>
          )}
          {draft?.suggestion_only && (
            <p className="banner caution">
              Suggestion only: read the order and policies yourself before sending.
            </p>
          )}
          {draft && editing && (
            <DraftEditor
              ticketId={ticket.ticket_id}
              initial={shownText}
              onDone={() => setEditing(false)}
            />
          )}
          {draft && !editing && (
            <>
              <p className="reply">{shownText}</p>
              {draft.review_status !== "pending" && (
                <p className="hint">
                  {humanize(draft.review_status)}
                  {draft.reviewed_by ? ` by ${draft.reviewed_by}` : ""}.
                </p>
              )}
              {actionable && (
                <div className="actions">
                  <button type="button" className="primary" onClick={() => setDialog("approve")}>
                    Approve (A)
                  </button>
                  <button type="button" onClick={() => setEditing(true)}>
                    Edit (E)
                  </button>
                </div>
              )}
            </>
          )}
        </section>

        <EvidencePanel ticket={ticket} />
      </div>

      {events.length > 0 && <ActivityList events={events} />}
      <p>
        <Link to={`/audit/${ticket.ticket_id}`}>Full audit log for this ticket</Link>
      </p>
      <p>
        <button type="button" onClick={() => setDialog("erase")}>
          Erase this customer&apos;s data
        </button>
      </p>

      {dialog === "approve" && (
        <ApproveDialog
          ticketId={ticket.ticket_id}
          originalViewed={viewed}
          suggestionOnly={draft?.suggestion_only ?? false}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === "erase" && (
        <EraseCustomerDialog
          email={ticket.customer_email}
          onClose={() => setDialog(null)}
          onErased={() => navigate("/tickets")}
        />
      )}
    </>
  );
}
