import type { TicketStatus } from "../api/tickets";

export interface QueueTab {
  id: string;
  label: string;
  statuses: readonly TicketStatus[];
}

/**
 * "All" lists every ticket (no status filter). "In progress" is the owner's working list: drafted
 * tickets wait there for a decision. Every other tab is one stage of a ticket's life.
 */
export const QUEUE_TABS: readonly QueueTab[] = [
  { id: "all", label: "All", statuses: [] },
  { id: "in-progress", label: "In progress", statuses: ["classified", "drafted"] },
  { id: "new", label: "New", statuses: ["new"] },
  { id: "escalated", label: "Escalated", statuses: ["escalated"] },
  { id: "approved", label: "Approved", statuses: ["approved", "sent"] },
  { id: "rejected", label: "Rejected", statuses: ["rejected"] },
  { id: "failed", label: "Failed", statuses: ["failed"] },
];

/** The stage tab a ticket with this status is listed under. "All" holds every ticket, so it is
 * never the answer. */
export function tabForStatus(status: TicketStatus): QueueTab {
  return QUEUE_TABS.find((t) => t.statuses.includes(status)) ?? QUEUE_TABS[0]!;
}

/** The queue address for a tab and page (1 is the first page), so Back can return to it. */
export function queueAddress(tabId: string, page = 1): string {
  const params = new URLSearchParams();
  if (tabId !== QUEUE_TABS[0]!.id) params.set("tab", tabId);
  if (page > 1) params.set("page", String(page));
  const query = params.toString();
  return query ? `/tickets?${query}` : "/tickets";
}
