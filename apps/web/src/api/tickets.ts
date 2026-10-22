import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type { components } from "./schema";

export type TicketStatus = components["schemas"]["TicketStatus"];
export type TicketSummary = components["schemas"]["TicketSummary"];

export const PAGE_SIZE = 25;
export const POLL_MS = 30_000;

export function useTickets(statuses: readonly TicketStatus[], page: number) {
  return useQuery({
    queryKey: ["tickets", [...statuses], page],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets", {
          params: {
            query: { status: [...statuses], limit: PAGE_SIZE, offset: page * PAGE_SIZE },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: POLL_MS,
  });
}
