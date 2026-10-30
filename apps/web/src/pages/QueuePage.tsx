import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { useLogout, useMe } from "../api/auth";
import { PAGE_SIZE, useTickets } from "../api/tickets";
import { ContactForm } from "../components/ContactForm";
import { Modal } from "../components/Modal";
import { TicketRow } from "../components/TicketRow";
import { useListKeys } from "../hooks/useListKeys";
import { useTitle } from "../hooks/useTitle";
import { useWakingUp } from "../hooks/useWakingUp";
import { QUEUE_TABS } from "./queueTabs";

export function QueuePage() {
  useTitle("Queue");
  const [tabId, setTabId] = useState(QUEUE_TABS[0]!.id);
  const [page, setPage] = useState(0);
  const [adding, setAdding] = useState(false);
  const queryClient = useQueryClient();
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
          <Link to="/metrics">Metrics</Link>
          <Link to="/audit">Audit log</Link>
          <button type="button" onClick={() => setAdding(true)}>
            New complaint
          </button>
          {me.data?.email}{" "}
          <button type="button" onClick={() => logout.mutate()}>
            Sign out
          </button>
        </span>
      </header>
      <main id="main" tabIndex={-1}>
        <div
          role="tablist"
          aria-label="Ticket status"
          className="tabs"
          onKeyDown={(event) => {
            const at = QUEUE_TABS.findIndex((t) => t.id === tab.id);
            const to =
              event.key === "ArrowRight"
                ? (at + 1) % QUEUE_TABS.length
                : event.key === "ArrowLeft"
                  ? (at - 1 + QUEUE_TABS.length) % QUEUE_TABS.length
                  : event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? QUEUE_TABS.length - 1
                      : -1;
            if (to < 0) return;
            event.preventDefault();
            setTabId(QUEUE_TABS[to]!.id);
            setPage(0);
            document.getElementById(`tab-${QUEUE_TABS[to]!.id}`)?.focus();
          }}
        >
          {QUEUE_TABS.map((t) => (
            <button
              key={t.id}
              id={`tab-${t.id}`}
              type="button"
              role="tab"
              aria-selected={t.id === tab.id}
              aria-controls="queue-panel"
              tabIndex={t.id === tab.id ? 0 : -1}
              onClick={() => {
                setTabId(t.id);
                setPage(0);
              }}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div role="tabpanel" id="queue-panel" aria-labelledby={`tab-${tab.id}`}>
          {tickets.isPending && (
            <div role="status" aria-live="polite">
              <p>
                {waking ? "The server is waking up. This can take up to a minute." : "Loading…"}
              </p>
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
        </div>
        <p className="hint">Keys: J and K move down and up the list, Enter opens a ticket.</p>
      </main>
      {adding && (
        <Modal title="New complaint" onClose={() => setAdding(false)}>
          <ContactForm
            mode="owner"
            onSent={() => void queryClient.invalidateQueries({ queryKey: ["tickets"] })}
            onClose={() => setAdding(false)}
          />
        </Modal>
      )}
    </div>
  );
}
