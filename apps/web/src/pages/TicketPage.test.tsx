// @vitest-environment jsdom
import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RequireOwner } from "../components/RequireOwner";
import { renderAt, stubApi, type Routing } from "../test-support";
import { TicketPage } from "./TicketPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ID = "11111111-1111-4111-8111-111111111111";

const draft = (over: Record<string, unknown> = {}) => ({
  draft_id: "22222222-2222-4222-8222-222222222222",
  attempt: 1,
  kind: "llm",
  suggestion_only: false,
  reply_text: "Your order RD-10001 was delivered on 2026-10-19.",
  final_text: null,
  citations: [
    { claim: "RD-10001", source_type: "order_field", source_ref: "orders.order_id" },
    { claim: "30 days", source_type: "policy", source_ref: "RET-1.1" },
  ],
  cited_policies: [{ policy_id: "RET-1.1", text: "Returns are accepted for 30 days." }],
  proposed_action: "none",
  confidence: 0.9,
  model: "gemini",
  policy_version: "v1",
  grounding_result: { passed: true, deterministic: [], judge: { grounded: true, issues: [] } },
  review_status: "pending",
  reviewed_by: null,
  reviewed_at: null,
  created_at: "2026-10-22T10:00:00Z",
  ...over,
});

const detail = (over: Record<string, unknown> = {}) => ({
  ticket_id: ID,
  thread_id: "33333333-3333-4333-8333-333333333333",
  received_at: "2026-10-22T10:00:00Z",
  channel: "form",
  customer_email: "asha@example.com",
  customer_name: "Asha",
  subject: "Return my water bottle",
  body: "Please help with my order.",
  order_id_hint: null,
  has_photo: false,
  status: "drafted",
  intent: "return",
  urgency: "normal",
  confidence: 0.9,
  sentiment: "neutral",
  flags: [],
  escalation_reason: null,
  failure_reason: null,
  order: { order_id: "RD-10001", delivered_at: "2026-10-19", items: [{ sku: "x" }] },
  draft: draft(),
  needs_original_viewed: false,
  ...over,
});

function app(ticket: unknown, extra: Routing = () => undefined) {
  return stubApi((method, p, search) => {
    if (p === "/api/v1/auth/me") return { body: { email: "owner@example.com" } };
    if (p === `/api/v1/tickets/${ID}` && method === "GET") return { body: ticket };
    if (p === `/api/v1/tickets/${ID}/audit`) return { body: [] };
    return extra(method, p, search);
  });
}

function show() {
  renderAt(
    `/tickets/${ID}`,
    <Route
      path="/tickets/:ticketId"
      element={
        <RequireOwner>
          <TicketPage />
        </RequireOwner>
      }
    />,
  );
}

const outcome = { body: { ticket_id: ID, ticket_status: "sent", review_status: "approved" } };

describe("the ticket screen", () => {
  it("shows the message, the draft and the evidence side by side", async () => {
    app(detail());
    show();
    expect(await screen.findByText("Please help with my order.")).toBeTruthy();
    expect(screen.getByText(/was delivered on 2026-10-19/)).toBeTruthy();
    expect(screen.getByText("Returns are accepted for 30 days.")).toBeTruthy();
    expect(screen.getByText(/= RD-10001/)).toBeTruthy();
    expect(screen.getByText("Every claim is backed.")).toBeTruthy();
  });

  it("hides a risky message until it is shown, and will not approve before that", async () => {
    const seen = app(
      detail({ needs_original_viewed: true, flags: ["prompt_injection_suspected"] }),
      (m, p) => (m === "POST" && p.endsWith("/approve") ? outcome : undefined),
    );
    show();
    expect(await screen.findByText(/hidden until you choose/)).toBeTruthy();
    expect(screen.queryByText("Please help with my order.")).toBeNull();

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Approve/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Approve" }).hasAttribute("disabled")).toBe(
      true,
    );
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await user.click(screen.getByRole("button", { name: "Show original message" }));
    expect(screen.getByText("Please help with my order.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Approve/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(seen.some((r) => r.path.endsWith("/approve"))).toBe(true));
    const sent = JSON.parse(seen.find((r) => r.path.endsWith("/approve"))!.body);
    expect(sent).toEqual({ original_viewed: true, confirm_suggestion_only: false });
  });

  it("shows an escalated ticket's reason as an escalation", async () => {
    app(detail({ status: "escalated", escalation_reason: "safety", draft: null }));
    show();
    expect(await screen.findByText("Escalated: Safety")).toBeTruthy();
  });

  it("calls a drafted ticket with a reason flagged, not escalated", async () => {
    app(detail({ escalation_reason: "outside_policy" }));
    show();
    expect(await screen.findByText("Flagged: Outside policy")).toBeTruthy();
    expect(screen.queryByText(/Escalated:/)).toBeNull();
  });

  it("lets the owner answer an escalated ticket, but only after the message is shown", async () => {
    const seen = app(
      detail({
        status: "escalated",
        escalation_reason: "legal_threat",
        draft: null,
        needs_original_viewed: true,
      }),
      (m, p) => (m === "POST" && p.endsWith("/owner-reply") ? outcome : undefined),
    );
    show();
    const user = userEvent.setup();
    const send = await screen.findByRole("button", { name: "Send reply (mock)" });
    await user.type(screen.getByRole("textbox", { name: /Your reply/ }), "I will call you today.");
    expect(send.hasAttribute("disabled")).toBe(true); // the original has not been shown yet
    await user.click(screen.getByRole("button", { name: "Show original message" }));
    await user.click(send);
    await waitFor(() => expect(seen.some((r) => r.path.endsWith("/owner-reply"))).toBe(true));
    expect(JSON.parse(seen.find((r) => r.path.endsWith("/owner-reply"))!.body)).toEqual({
      reply_text: "I will call you today.",
      original_viewed: true,
      override: null,
    });
  });

  it("shows the warnings on an owner reply and sends past them only with a reason", async () => {
    let calls = 0;
    const seen = app(
      detail({
        status: "escalated",
        escalation_reason: "legal_threat",
        draft: null,
        needs_original_viewed: true,
      }),
      (m, p) => {
        if (m !== "POST" || !p.endsWith("/owner-reply")) return undefined;
        calls += 1;
        return calls === 1
          ? {
              status: 422,
              body: {
                detail: {
                  message: "this text has grounding warnings; give a reason to save it anyway",
                  failures: [{ rule_id: "G-3", span: "refund", detail: "promises an outcome" }],
                },
              },
            }
          : outcome;
      },
    );
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Show original message" }));
    await user.type(screen.getByRole("textbox", { name: /Your reply/ }), "We will refund you.");
    await user.click(screen.getByRole("button", { name: "Send reply (mock)" }));
    expect(await screen.findByText("promises an outcome")).toBeTruthy();
    const anyway = screen.getByRole("button", { name: "Send anyway (mock)" });
    expect(anyway.hasAttribute("disabled")).toBe(true);
    await user.type(screen.getByRole("textbox", { name: /Why is it right/ }), "I will refund it");
    await user.click(anyway);
    await waitFor(() => expect(calls).toBe(2));
    const second = JSON.parse(seen.filter((r) => r.path.endsWith("/owner-reply"))[1]!.body);
    expect(second.override).toEqual({ reason: "I will refund it", overridden_rules: ["G-3"] });
  });

  it("offers no reply box on a ticket that is not escalated", async () => {
    app(detail());
    show();
    await screen.findByText("Draft reply");
    expect(screen.queryByRole("textbox", { name: /Your reply/ })).toBeNull();
  });

  it("asks for a second confirmation on a suggestion-only draft", async () => {
    const seen = app(detail({ draft: draft({ suggestion_only: true }) }), (m, p) =>
      m === "POST" && p.endsWith("/approve") ? outcome : undefined,
    );
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Approve/ }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Approve" });
    expect(confirm.hasAttribute("disabled")).toBe(true);
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(confirm);
    await waitFor(() => expect(seen.some((r) => r.path.endsWith("/approve"))).toBe(true));
    expect(
      JSON.parse(seen.find((r) => r.path.endsWith("/approve"))!.body).confirm_suggestion_only,
    ).toBe(true);
  });

  it("rejects only with a reason of ten characters", async () => {
    const seen = app(detail(), (m, p) =>
      m === "POST" && p.endsWith("/reject")
        ? { body: { ...outcome.body, review_status: "rejected" } }
        : undefined,
    );
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Reject/ }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Reject" });
    await user.type(within(dialog).getByRole("textbox"), "too short");
    expect(confirm.hasAttribute("disabled")).toBe(true);
    await user.type(within(dialog).getByRole("textbox"), " and now long enough");
    await user.click(confirm);
    await waitFor(() => expect(seen.some((r) => r.path.endsWith("/reject"))).toBe(true));
    expect(JSON.parse(seen.find((r) => r.path.endsWith("/reject"))!.body).reason).toBe(
      "too short and now long enough",
    );
  });

  it("warns on an ungrounded edit and saves it only with a reason", async () => {
    const failure = {
      rule_id: "G-1",
      span: "RD-10002",
      detail: "order number is not cited",
      citation_missing: false,
    };
    const seen = app(detail(), (m, p) => {
      if (p.endsWith("/draft/validate")) return { body: { passed: false, failures: [failure] } };
      if (m === "PATCH") return outcome;
      return undefined;
    });
    show();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: /Edit/ }));
    const reply = screen.getByLabelText("Reply to the customer");
    await user.clear(reply);
    await user.type(reply, "About RD-10002");
    expect(await screen.findByText(/order number is not cited/)).toBeTruthy();
    const save = screen.getByRole("button", { name: "Save edit" });
    expect(save.hasAttribute("disabled")).toBe(true);
    await user.type(
      screen.getByLabelText("Why is it right to send this anyway?"),
      "customer asked for it",
    );
    await user.click(save);
    await waitFor(() => expect(seen.some((r) => r.method === "PATCH")).toBe(true));
    expect(JSON.parse(seen.find((r) => r.method === "PATCH")!.body)).toEqual({
      reply_text: "About RD-10002",
      override: { reason: "customer asked for it", overridden_rules: ["G-1"] },
    });
  });

  it("opens the approve dialog on A and closes it on Escape", async () => {
    app(detail());
    show();
    await screen.findByText("Please help with my order.");
    const user = userEvent.setup();
    await user.keyboard("a");
    expect(screen.getByRole("dialog")).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("does not claim a fallback reply was grounded", async () => {
    app(detail({ draft: draft({ kind: "fallback_template", citations: [], cited_policies: [] }) }));
    show();
    expect(await screen.findByText(/Standard reply\. It makes no claims/)).toBeTruthy();
    expect(screen.queryByText("Every claim is backed.")).toBeNull();
  });

  it("shows why a ticket failed and offers no actions without a draft", async () => {
    app(
      detail({
        status: "failed",
        draft: null,
        order: null,
        failure_reason: "gave up after 3 attempts",
      }),
    );
    show();
    expect(await screen.findByText(/gave up after 3 attempts/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Approve/ })).toBeNull();
    expect(screen.getByText("No order was found for this ticket.")).toBeTruthy();
  });
});
