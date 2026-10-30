import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { PAGE_SIZE, useTickets } from "../api/tickets";
import { QueueSidebar } from "../components/QueueSidebar";
import { TopBar } from "../components/TopBar";
import { TicketRow } from "../components/TicketRow";
import { useListKeys } from "../hooks/useListKeys";
import { useTitle } from "../hooks/useTitle";
import { useWakingUp } from "../hooks/useWakingUp";
import {
  QUEUE_TABS,
  filtersActive,
  queueAddress,
  readFilters,
  type QueueFilters,
} from "./queueTabs";

export function QueuePage() {
  useTitle("Queue");
  // The tab and page live in the address, so Back, a refresh and a shared link all return here.
  const [params, setParams] = useSearchParams();
  const tab = QUEUE_TABS.find((t) => t.id === params.get("tab")) ?? QUEUE_TABS[0]!;
  const pageParam = Number(params.get("page"));
  const page = Number.isInteger(pageParam) && pageParam > 1 ? pageParam - 1 : 0;
  const filters = readFilters(params);
  const here = queueAddress(tab.id, page + 1, filters);
  function go(tabId: string, pageIndex: number, next: QueueFilters = filters) {
    const target = new URL(queueAddress(tabId, pageIndex + 1, next), "http://x");
    setParams(target.searchParams, { replace: true });
  }
  const tickets = useTickets(tab.statuses, page, filters);
  const waking = useWakingUp(tickets.isPending);
  const items = tickets.data?.items ?? [];
  const navigate = useNavigate();
  const selected = useListKeys(items.length, (index) => {
    const ticket = items[index];
    if (ticket) navigate(`/tickets/${ticket.ticket_id}`, { state: { from: here } });
  });
  const total = tickets.data?.total ?? 0;
  const now = Date.now();

  return (
    <div className="app">
      <TopBar>
        <Link to="/contact">New complaint</Link>
      </TopBar>
      <main id="main" tabIndex={-1} className="wide">
        <div className="queue-layout">
          <QueueSidebar filters={filters} onChange={(next) => go(tab.id, 0, next)} />
          <div className="queue-content">
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
                go(QUEUE_TABS[to]!.id, 0);
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
                  onClick={() => go(t.id, 0)}
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
                <p className="status empty">
                  {filtersActive(filters)
                    ? `Nothing in “${tab.label}” matches these filters.`
                    : `Nothing in “${tab.label}” right now.`}
                </p>
              )}

              {items.length > 0 && (
                <ul className="tickets" aria-label={`${tab.label} tickets`}>
                  {items.map((ticket, index) => (
                    <TicketRow
                      key={ticket.ticket_id}
                      ticket={ticket}
                      selected={index === selected}
                      now={now}
                      returnTo={here}
                    />
                  ))}
                </ul>
              )}

              {total > PAGE_SIZE && (
                <nav className="pager" aria-label="Pages">
                  <button type="button" disabled={page === 0} onClick={() => go(tab.id, page - 1)}>
                    Previous
                  </button>
                  <span>
                    {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, total)} of {total}
                  </span>
                  <button
                    type="button"
                    disabled={(page + 1) * PAGE_SIZE >= total}
                    onClick={() => go(tab.id, page + 1)}
                  >
                    Next
                  </button>
                </nav>
              )}
            </div>
            <p className="hint">Keys: J and K move down and up the list, Enter opens a ticket.</p>
          </div>
        </div>
      </main>
    </div>
  );
}
