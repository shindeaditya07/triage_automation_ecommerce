import type { AuditEvent, TicketDetail } from "../api/tickets";
import { humanize } from "../format";
import { orderFacts, orderValue, readGrounding } from "../ticketView";

export function EvidencePanel({ ticket }: { ticket: TicketDetail }) {
  const draft = ticket.draft;
  const grounding = readGrounding(draft?.grounding_result ?? null);
  const facts = orderFacts(ticket.order);
  return (
    <section aria-label="Evidence" className="panel">
      <h2>Evidence</h2>

      <h3>Ticket</h3>
      <dl className="facts">
        <dt>Intent</dt>
        <dd>{ticket.intent ? humanize(ticket.intent) : "Not classified"}</dd>
        <dt>Urgency</dt>
        <dd>{ticket.urgency ? humanize(ticket.urgency) : "Not classified"}</dd>
        <dt>Sentiment</dt>
        <dd>{ticket.sentiment ? humanize(ticket.sentiment) : "Unknown"}</dd>
        <dt>Classifier confidence</dt>
        <dd>{ticket.confidence === null ? "Unknown" : ticket.confidence.toFixed(2)}</dd>
        {draft && (
          <>
            <dt>Draft confidence</dt>
            <dd>{draft.confidence.toFixed(2)}</dd>
          </>
        )}
      </dl>
      {ticket.flags.length > 0 && (
        <p>
          {ticket.flags.map((flag) => (
            <span key={flag} className="badge caution">
              {humanize(flag)}
            </span>
          ))}
        </p>
      )}

      {draft && (
        <>
          <h3>Grounding</h3>
          {draft.kind === "fallback_template" ? (
            <p className="hint">
              Standard reply. It makes no claims from the order or policies, so there is nothing to
              check.
            </p>
          ) : (
            <>
              {grounding === null && <p className="hint">No grounding result stored.</p>}
              {grounding?.passed === true && <p className="ok">Every claim is backed.</p>}
              {grounding && grounding.passed !== true && (
                <p className="warn-text">The checks did not all pass.</p>
              )}
              {grounding?.judgeError && <p className="warn-text">Judge: {grounding.judgeError}</p>}
              {grounding && grounding.failures.length > 0 && (
                <ul>
                  {grounding.failures.map((f, i) => (
                    <li key={i}>
                      <strong>{f.ruleId}</strong> {f.detail}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}

          <h3>Cited order fields and policies</h3>
          {draft.citations.length === 0 && <p className="hint">The draft cites nothing.</p>}
          <ul className="citations">
            {draft.citations.map((c, i) => {
              const value =
                c.source_type === "order_field"
                  ? orderValue(ticket.order, c.source_ref)
                  : undefined;
              return (
                <li key={i}>
                  <code>{c.source_ref}</code>
                  {value !== undefined && <> = {value}</>}
                  <span className="hint"> for “{c.claim}”</span>
                </li>
              );
            })}
          </ul>
          {draft.cited_policies.map((p) => (
            <blockquote key={p.policy_id}>
              <strong>{p.policy_id}</strong>
              <p>{p.text ?? "This passage is not in the loaded policy set."}</p>
            </blockquote>
          ))}
        </>
      )}

      <h3>Order</h3>
      {facts.length === 0 ? (
        <p className="hint">No order was found for this ticket.</p>
      ) : (
        <dl className="facts">
          {facts.map(([k, v]) => (
            <div key={k} className="fact">
              <dt>{humanize(k)}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

export function ActivityList({ events }: { events: AuditEvent[] }) {
  return (
    <section aria-label="Activity" className="panel">
      <h2>Activity</h2>
      <ol className="activity">
        {events.map((e) => (
          <li key={e.event_id}>
            <details>
              <summary>
                {humanize(e.event_type)} · {e.actor} ·{" "}
                <time dateTime={e.occurred_at}>{new Date(e.occurred_at).toLocaleString()}</time>
              </summary>
              <pre>{JSON.stringify(e.payload, null, 2)}</pre>
            </details>
          </li>
        ))}
      </ol>
    </section>
  );
}
