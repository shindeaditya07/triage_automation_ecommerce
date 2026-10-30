import type { TicketStatus } from "../api/tickets";
import type { components } from "../api/schema";

export type Urgency = components["schemas"]["Urgency"];
export type Intent = components["schemas"]["Intent"];
export type TicketSort = components["schemas"]["TicketSort"];

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
  { id: "in-progress", label: "In progress", statuses: ["new", "classified", "drafted"] },
  { id: "escalated", label: "Escalated", statuses: ["escalated"] },
  { id: "approved", label: "Approved", statuses: ["approved", "sent"] },
  { id: "failed", label: "Failed", statuses: ["failed"] },
];

/** The stage tab a ticket with this status is listed under. "All" holds every ticket, so it is
 * never the answer. A status with no tab of its own (a ticket rejected before the Reject action
 * was removed) is only listed under All. */
export function tabForStatus(status: TicketStatus): QueueTab | undefined {
  return QUEUE_TABS.slice(1).find((t) => t.statuses.includes(status));
}

/** Most urgent first, the order the Metrics page uses for its urgency split. */
export const URGENCIES: readonly Urgency[] = ["critical", "high", "normal", "low"];

/** The intents the Metrics page breaks tickets down by. */
export const INTENTS: readonly Intent[] = [
  "order_status",
  "refund",
  "return",
  "damaged_item",
  "product_question",
  "cancellation",
  "address_change",
  "payment",
  "warranty",
  "other",
];

export const SORTS: readonly { id: TicketSort; label: string }[] = [
  { id: "newest", label: "Newest first" },
  { id: "oldest", label: "Oldest first" },
  { id: "urgency", label: "Most urgent first" },
  { id: "confidence_low", label: "Lowest confidence first" },
  { id: "confidence_high", label: "Highest confidence first" },
];

/** What the sidebar narrows and orders the list by. An empty list means "any". */
export interface QueueFilters {
  urgency: readonly Urgency[];
  intent: readonly Intent[];
  sort: TicketSort;
}

export const NO_FILTERS: QueueFilters = { urgency: [], intent: [], sort: "newest" };

export function filtersActive(filters: QueueFilters): boolean {
  return filters.urgency.length > 0 || filters.intent.length > 0;
}

/** Reads the filters out of an address; a value this page does not know is ignored. */
export function readFilters(params: URLSearchParams): QueueFilters {
  const pick = <T extends string>(name: string, known: readonly T[]): T[] => {
    const wanted = (params.get(name) ?? "").split(",");
    return known.filter((value) => wanted.includes(value));
  };
  const sort = SORTS.find((s) => s.id === params.get("sort"))?.id ?? NO_FILTERS.sort;
  return { urgency: pick("urgency", URGENCIES), intent: pick("intent", INTENTS), sort };
}

/** The queue address for a tab, page (1 is the first) and filters, so Back can return to it. */
export function queueAddress(tabId: string, page = 1, filters: QueueFilters = NO_FILTERS): string {
  const params = new URLSearchParams();
  if (tabId !== QUEUE_TABS[0]!.id) params.set("tab", tabId);
  if (page > 1) params.set("page", String(page));
  if (filters.urgency.length > 0) params.set("urgency", filters.urgency.join(","));
  if (filters.intent.length > 0) params.set("intent", filters.intent.join(","));
  if (filters.sort !== NO_FILTERS.sort) params.set("sort", filters.sort);
  const query = params.toString();
  return query ? `/tickets?${query}` : "/tickets";
}
