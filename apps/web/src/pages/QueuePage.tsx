import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { useLogout, useMe } from "../api/auth";
import { PAGE_SIZE, useTickets } from "../api/tickets";
import { TicketRow } from "../components/TicketRow";
import { useListKeys } from "../hooks/useListKeys";
import { useWakingUp } from "../hooks/useWakingUp";
import { QUEUE_TABS } from "./queueTabs";

export function QueuePage() {
  const [tabId, setTabId] = useState(QUEUE_TABS[0]!.id);
  const [page, setPage] = useState(0);
  const tab = QUEUE_TABS.find((t) => t.id === tabId) ?? QUEUE_TABS[0]!;
  const tickets = useTickets(tab.statuses, page);
  const me = useMe();
  const logout = useLogout();
  const waking = useWakingUp(tickets.isPending);
  const items = tickets.data?.items ?? [];
  const navigate = useNavigate();
  const selected = useListKeys(items.length, (index) => {
    const ticket = items[index];
    if (ticket) navigate(`/tickets/${ticket.ticket_id}`);
  });
  const total = tickets.data?.total ?? 0;
  const now = Date.now();

  return (
    <div className="app">
      <header className="topbar">
        <h1>ReplyDesk</h1>
        <span className="who">
          {me.data?.email}{" "}
          <button type="button" onClick={() => logout.mutate()}>
            Sign out
          </button>
        </span>
      </header>
      <main>
        <div role="tablist" aria-label="Ticket status" className="tabs">
          {QUEUE_TABS.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={t.id === tab.id}
              onClick={() => {
                setTabId(t.id);
                setPage(0);
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tickets.isPending && (
          <div role="status" aria-live="polite">
            <p>{waking ? "The server is waking up. This can take up to a minute." : "Loading…"}</p>
            <ul className="tickets skeleton" aria-hidden="true">
              <li />
              <li />
              <li />
            </ul>
          </div>
        )}

        {tickets.isError && (
          <p className="status error" role="alert">
            Could not load tickets.{" "}
            <button type="button" onClick={() => void tickets.refetch()}>
              Retry
            </button>
          </p>
        )}

        {tickets.data && items.length === 0 && (
          <p className="status empty">Nothing in “{tab.label}” right now.</p>
        )}

        {items.length > 0 && (
          <ul className="tickets" aria-label={`${tab.label} tickets`}>
            {items.map((ticket, index) => (
              <TicketRow
                key={ticket.ticket_id}
                ticket={ticket}
                selected={index === selected}
                now={now}
              />
            ))}
          </ul>
        )}

        {total > PAGE_SIZE && (
          <nav className="pager" aria-label="Pages">
            <button type="button" disabled={page === 0} onClick={() => setPage(page - 1)}>
              Previous
            </button>
            <span>
              {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
            </span>
            <button
              type="button"
              disabled={(page + 1) * PAGE_SIZE >= total}
              onClick={() => setPage(page + 1)}
            >
              Next
            </button>
          </nav>
        )}
        <p className="hint">Keys: J and K move down and up the list, Enter opens a ticket.</p>
      </main>
    </div>
  );
}
