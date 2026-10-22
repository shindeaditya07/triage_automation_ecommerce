import type { TicketSummary } from "../api/tickets";
import { ageLabel, humanize } from "../format";

interface Props {
  ticket: TicketSummary;
  selected: boolean;
  now: number;
}

export function TicketRow({ ticket, selected, now }: Props) {
  const injection = ticket.flags.includes("prompt_injection_suspected");
  const classes = ["ticket"];
  if (ticket.status === "escalated") classes.push("escalated");
  if (selected) classes.push("selected");
  return (
    <li className={classes.join(" ")} aria-current={selected ? "true" : undefined}>
      <div className="ticket-main">
        <span className="ticket-subject">{ticket.subject || "(no subject)"}</span>
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
      </div>
    </li>
  );
}
