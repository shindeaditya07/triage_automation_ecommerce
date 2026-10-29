import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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

export type TicketDetail = components["schemas"]["TicketDetail"];
export type AuditEvent = components["schemas"]["AuditEvent"];
export type RuleFailure = components["schemas"]["RuleFailure"];
export type SaveEditRequest = components["schemas"]["SaveEditRequest"];
export type ApproveRequest = components["schemas"]["ApproveRequest"];
export type OwnerReplyRequest = components["schemas"]["OwnerReplyRequest"];

export function useTicket(ticketId: string) {
  return useQuery({
    queryKey: ["ticket", ticketId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/tickets/{ticket_id}", { params: { path: { ticket_id: ticketId } } })),
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

export function useReject(ticketId: string) {
  const after = useAfterReview(ticketId);
  return useMutation({
    mutationFn: (reason: string) =>
      unwrap(
        api.POST("/api/v1/tickets/{ticket_id}/reject", {
          params: { path: { ticket_id: ticketId } },
          body: { reason },
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
