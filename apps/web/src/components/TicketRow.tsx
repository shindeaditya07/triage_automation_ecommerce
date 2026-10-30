import { Link } from "react-router-dom";

import type { TicketSummary } from "../api/tickets";
import { ageLabel, complaintTitle, humanize } from "../format";
import { tabForStatus } from "../pages/queueTabs";

interface Props {
  ticket: TicketSummary;
  selected: boolean;
  now: number;
  /** The queue address this row was listed on, so the ticket page can go back to it. */
  returnTo: string;
}

export function TicketRow({ ticket, selected, now, returnTo }: Props) {
  const tab = tabForStatus(ticket.status);
  const injection = ticket.flags.includes("prompt_injection_suspected");
  const classes = ["ticket"];
  if (ticket.status === "escalated") classes.push("escalated");
  if (selected) classes.push("selected");
  return (
    <li className={classes.join(" ")} aria-current={selected ? "true" : undefined}>
      <div className="ticket-main">
        <Link
          className="ticket-subject"
          to={`/tickets/${ticket.ticket_id}`}
          state={{ from: returnTo }}
        >
          {complaintTitle(ticket.complaint_id, ticket.subject)}
        </Link>
        <span className="ticket-from">
          {ticket.customer_name ? `${ticket.customer_name} · ` : ""}
          {ticket.customer_email}
        </span>
        {ticket.escalation_reason && (
          <span className="ticket-reason">{humanize(ticket.escalation_reason)}</span>
        )}
      </div>
      <div className="ticket-badges">
        {injection && (
          <span className="badge warn" title="The message looks like it tries to instruct the AI">
            ⚠ Suspected injection
          </span>
        )}
        {ticket.suggestion_only && <span className="badge caution">Suggestion only</span>}
        {ticket.used_fallback_template && <span className="badge caution">Fallback reply</span>}
        {ticket.urgency && (
          <span className={`badge urgency-${ticket.urgency}`}>{humanize(ticket.urgency)}</span>
        )}
        {ticket.intent && <span className="badge">{humanize(ticket.intent)}</span>}
        <span className="badge muted">{humanize(ticket.status)}</span>
      </div>
      <div className="ticket-meta">
        <span>{humanize(ticket.channel)}</span>
        <time dateTime={ticket.received_at} title={new Date(ticket.received_at).toLocaleString()}>
          {ageLabel(ticket.received_at, now)}
        </time>
        <span className="ticket-tab" title={`Listed under the ${tab.label} tab`}>
          {tab.label}
        </span>
      </div>
    </li>
  );
}
