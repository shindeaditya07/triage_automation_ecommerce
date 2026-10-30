import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type { components } from "./schema";
import { NO_FILTERS, type QueueFilters } from "../pages/queueTabs";

export type TicketStatus = components["schemas"]["TicketStatus"];
export type TicketSummary = components["schemas"]["TicketSummary"];

export const PAGE_SIZE = 25;
export const POLL_MS = 30_000;

export function useTickets(
  statuses: readonly TicketStatus[],
  page: number,
  filters: QueueFilters = NO_FILTERS,
) {
  return useQuery({
    queryKey: ["tickets", [...statuses], page, filters],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets", {
          params: {
            query: {
              status: [...statuses],
              urgency: [...filters.urgency],
              intent: [...filters.intent],
              sort: filters.sort,
              limit: PAGE_SIZE,
              offset: page * PAGE_SIZE,
            },
          },
        }),
      ),
    placeholderData: keepPreviousData,
    refetchInterval: POLL_MS,
  });
}

export type BulkResult = components["schemas"]["BulkResult"];

/** Send one batch of complaints. The same batch id again makes no second ticket for any row. */
export function useBulkUpload() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (batch: { batchId: string; complaints: Record<string, string>[] }) =>
      unwrap(
        api.POST("/api/v1/tickets/bulk", {
          body: { batch_id: batch.batchId, complaints: batch.complaints },
        }),
      ),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tickets"] }),
  });
}

export type TicketDetail = components["schemas"]["TicketDetail"];
export type AuditEvent = components["schemas"]["AuditEvent"];
export type RuleFailure = components["schemas"]["RuleFailure"];
export type SaveEditRequest = components["schemas"]["SaveEditRequest"];
export type ApproveRequest = components["schemas"]["ApproveRequest"];
export type OwnerReplyRequest = components["schemas"]["OwnerReplyRequest"];

/** A ticket still being processed is read again every few seconds, so a retry shows its result. */
const PROCESSING_POLL_MS = 3_000;

export function useTicket(ticketId: string) {
  return useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/tickets/{ticket_id}", { params: { path: { ticket_id: ticketId } } })),
    refetchInterval: (query) =>
      ["new", "classified"].includes(query.state.data?.status ?? "") ? PROCESSING_POLL_MS : false,
  });
}

export function useAudit(ticketId: string) {
  return useQuery({
    queryKey: ["audit", ticketId],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/tickets/{ticket_id}/audit", { params: { path: { ticket_id: ticketId } } }),
      ),
  });
}

/** The grounding warning for an edit in progress. It never blocks saving. */
export function useValidateDraft(ticketId: string) {
  return useMutation({
    mutationFn: (reply_text: string) =>
      unwrap(
        api.POST("/api/v1/tickets/{ticket_id}/draft/validate", {
          params: { path: { ticket_id: ticketId } },
          body: { reply_text },
        }),
      ),
  });
}

/** After any review action the ticket, its history and every queue list are out of date. */
function useAfterReview(ticketId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["ticket", ticketId] }),
      queryClient.invalidateQueries({ queryKey: ["audit", ticketId] }),
      queryClient.invalidateQueries({ queryKey: ["tickets"] }),
    ]);
}

export function useSaveEdit(ticketId: string) {
  const after = useAfterReview(ticketId);
  return useMutation({
    mutationFn: (body: SaveEditRequest) =>
      unwrap(
        api.PATCH("/api/v1/tickets/{ticket_id}/draft", {
          params: { path: { ticket_id: ticketId } },
          body,
        }),
      ),
    onSuccess: after,
  });
}

export function useApprove(ticketId: string) {
  const after = useAfterReview(ticketId);
  return useMutation({
    mutationFn: (body: ApproveRequest) =>
      unwrap(
        api.POST("/api/v1/tickets/{ticket_id}/approve", {
          params: { path: { ticket_id: ticketId } },
          body,
        }),
      ),
    onSuccess: after,
  });
}

/** Run a failed ticket again from where it stopped. */
export function useRetry(ticketId: string) {
  const after = useAfterReview(ticketId);
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/tickets/{ticket_id}/retry", {
          params: { path: { ticket_id: ticketId } },
        }),
      ),
    onSuccess: after,
  });
}

/** The owner's own reply to an escalated ticket. It is checked, recorded and mock-sent. */
export function useOwnerReply(ticketId: string) {
  const after = useAfterReview(ticketId);
  return useMutation({
    mutationFn: (body: OwnerReplyRequest) =>
      unwrap(
        api.POST("/api/v1/tickets/{ticket_id}/owner-reply", {
          params: { path: { ticket_id: ticketId } },
          body,
        }),
      ),
    onSuccess: after,
  });
}

/** Erase every ticket this customer sent. The address goes in the URL path, so it is encoded. */
export function useEraseCustomer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (email: string) =>
      unwrap(api.DELETE("/api/v1/customers/{email}", { params: { path: { email } } })),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["tickets"] }),
  });
}
