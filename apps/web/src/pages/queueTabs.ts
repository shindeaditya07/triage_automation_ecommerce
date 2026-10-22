import type { TicketStatus } from "../api/tickets";

export interface QueueTab {
  id: string;
  label: string;
  statuses: readonly TicketStatus[];
}

/** The owner's working list is "In progress": drafted tickets wait there for a decision. */
export const QUEUE_TABS: readonly QueueTab[] = [
  { id: "in-progress", label: "In progress", statuses: ["classified", "drafted"] },
  { id: "new", label: "New", statuses: ["new"] },
  { id: "escalated", label: "Escalated", statuses: ["escalated"] },
  { id: "approved", label: "Approved", statuses: ["approved", "sent"] },
  { id: "rejected", label: "Rejected", statuses: ["rejected"] },
  { id: "failed", label: "Failed", statuses: ["failed"] },
];
