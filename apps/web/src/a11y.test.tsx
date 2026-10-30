// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { createQueryClient } from "./api/queryClient";
import { REPORT } from "./metricsFixture";
import { a11yProblems, stubApi } from "./test-support";
import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.pushState({}, "", "/");
});

const ID = "11111111-1111-4111-8111-111111111111";
const summary = {
  ticket_id: ID,
  complaint_id: 48213907,
  received_at: "2026-10-22T10:00:00Z",
  channel: "form",
  customer_email: "a@example.com",
  customer_name: "Asha",
  subject: "Return my water bottle",
  status: "drafted",
  intent: "return",
  urgency: "high",
  escalation_reason: null,
  flags: ["prompt_injection_suspected"],
  has_draft: true,
  suggestion_only: true,
  used_fallback_template: false,
};
const draft = {
  draft_id: ID,
  attempt: 1,
  kind: "llm",
  suggestion_only: true,
  reply_text: "Hello",
  final_text: null,
  citations: [{ claim: "RD-1", source_type: "order_field", source_ref: "orders.order_id" }],
  cited_policies: [{ policy_id: "RET-1.1", text: "Returns in 30 days." }],
  proposed_action: "none",
  confidence: 0.9,
  model: "m",
  policy_version: "1",
  grounding_result: { passed: false, deterministic: [{ rule_id: "G-1", detail: "x" }] },
  review_status: "pending",
  reviewed_by: null,
  reviewed_at: null,
  created_at: "2026-10-22T10:00:00Z",
};
const detail = {
  ...summary,
  thread_id: ID,
  body: "Hello",
  order_id_hint: null,
  has_photo: false,
  confidence: 0.9,
  sentiment: "neutral",
  failure_reason: "gave up",
  order: { order_id: "RD-1", status: "delivered" },
  draft,
  needs_original_viewed: true,
};
const audit = [
  {
    event_id: 1,
    event_type: "ticket.drafted",
    actor: "system",
    occurred_at: summary.received_at,
    payload: { kind: "llm" },
  },
  {
    event_id: 2,
    event_type: "draft.edited",
    actor: "owner",
    occurred_at: summary.received_at,
    payload: { diff: "- a\n+ b" },
  },
];

function open(path: string, signedIn = true) {
  stubApi((_, p) => {
    if (p === "/api/v1/auth/me")
      return signedIn
        ? { body: { email: "owner@example.com" } }
        : { status: 401, body: { detail: "no" } };
    if (p === "/api/v1/tickets")
      return { body: { items: [summary], total: 1, limit: 25, offset: 0 } };
    if (p === `/api/v1/tickets/${ID}`) return { body: detail };
    if (p === `/api/v1/tickets/${ID}/audit`) return { body: audit };
    if (p === `/api/v1/tickets/${ID}/draft/validate`)
      return { body: { passed: true, failures: [] } };
    if (p === "/api/v1/metrics") return { body: REPORT };
    return undefined;
  });
  window.history.pushState({}, "", path);
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false, refetchInterval: false } });
  return render(
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>,
  );
}

describe("accessibility", () => {
  it("the checker does catch a problem", async () => {
    render(<input />);
    expect((await a11yProblems()).join()).toContain("label");
  });

  it("has a skip link, titles each page and moves focus to the content on navigation", async () => {
    open("/tickets");
    await screen.findByText("48213907 - Return my water bottle");
    expect(screen.getByRole("link", { name: "Skip to content" }).getAttribute("href")).toBe(
      "#main",
    );
    expect(document.title).toBe("Queue · ReplyDesk");
    await userEvent.setup().click(screen.getByRole("link", { name: "Metrics" }));
    await screen.findByText("Cost per ticket, by day");
    expect(document.title).toBe("Metrics · ReplyDesk");
    expect(document.activeElement?.id).toBe("main");
  });

  it("moves between the queue tabs with the arrow keys, one tab stop for the group", async () => {
    open("/tickets");
    await screen.findByText("48213907 - Return my water bottle");
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual([
      "0",
      "-1",
      "-1",
      "-1",
      "-1",
      "-1",
      "-1",
    ]);
    const user = userEvent.setup();
    tabs[0]!.focus();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(tabs[1]);
    expect(tabs[1]!.getAttribute("aria-selected")).toBe("true");
    await user.keyboard("{End}");
    expect(document.activeElement).toBe(tabs.at(-1));
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(tabs[0]);
    expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(tabs[0]!.id);
  });

  it("the login page has no axe violations", async () => {
    open("/login", false);
    await screen.findByRole("button", { name: /sign in/i });
    expect(await a11yProblems()).toEqual([]);
  });

  it("the queue has none", async () => {
    open("/tickets");
    await screen.findByText("48213907 - Return my water bottle");
    expect(await a11yProblems()).toEqual([]);
  });

  it("the ticket screen has none, with an editor and a dialog open", async () => {
    open(`/tickets/${ID}`);
    await screen.findByText(/hidden until you choose/);
    expect(await a11yProblems()).toEqual([]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: /Edit/ }));
    expect(await a11yProblems()).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: /Approve/ }));
    expect(await a11yProblems()).toEqual([]);
  });

  it("the metrics page has none", async () => {
    open("/");
    await screen.findByText("Cost per ticket, by day");
    expect(await a11yProblems()).toEqual([]);
  });

  it("the audit log page has none", async () => {
    open(`/audit/${ID}`);
    await screen.findByText("Draft ready for review");
    expect(await a11yProblems()).toEqual([]);
  });

  it("opens on the metrics page, with a way to the tickets and to sign out", async () => {
    open("/");
    await screen.findByText("Cost per ticket, by day");
    expect(document.title).toBe("Metrics · ReplyDesk");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Metrics" }).getAttribute("aria-current")).toBe("page");
    await userEvent.setup().click(screen.getByRole("link", { name: "Tickets" }));
    await screen.findByText("48213907 - Return my water bottle");
    expect(document.title).toBe("Queue · ReplyDesk");
    expect(window.location.pathname).toBe("/tickets");
  });

  it("sends the old /metrics address to the home page", async () => {
    open("/metrics");
    await screen.findByText("Cost per ticket, by day");
    expect(window.location.pathname).toBe("/");
  });

  it("goes back to the queue at /tickets from a ticket and from the audit log", async () => {
    open(`/tickets/${ID}`);
    const back = await screen.findByRole("link", { name: /Back to the queue/ });
    expect(back.getAttribute("href")).toBe("/tickets");
    cleanup();
    open(`/audit/${ID}`);
    await screen.findByText("Draft ready for review");
    expect(screen.getByRole("link", { name: /Back to the queue/ }).getAttribute("href")).toBe(
      "/tickets",
    );
  });

  it("keeps the queue tab in the address and Back returns to it from a ticket", async () => {
    open("/tickets?tab=escalated");
    await screen.findByText("48213907 - Return my water bottle");
    expect(screen.getByRole("tab", { name: "Escalated" }).getAttribute("aria-selected")).toBe(
      "true",
    );
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Approved" }));
    expect(window.location.search).toBe("?tab=approved");
    await user.click(screen.getByRole("tab", { name: "Escalated" }));
    await user.click(screen.getByRole("link", { name: "48213907 - Return my water bottle" }));
    const back = await screen.findByRole("link", { name: /Back to the queue/ });
    expect(back.getAttribute("href")).toBe("/tickets?tab=escalated");
    await user.click(back);
    await screen.findByText("48213907 - Return my water bottle");
    expect(window.location.search).toBe("?tab=escalated");
    expect(screen.getByRole("tab", { name: "Escalated" }).getAttribute("aria-selected")).toBe(
      "true",
    );
  });

  it("opens a ticket reached by a direct link with Back going to its own tab", async () => {
    open(`/tickets/${ID}`);
    const back = await screen.findByRole("link", { name: /Back to the queue/ });
    expect(back.getAttribute("href")).toBe("/tickets");
  });

  it("ignores a tab it does not know", async () => {
    open("/tickets?tab=nonsense&page=abc");
    await screen.findByText("48213907 - Return my water bottle");
    expect(screen.getByRole("tab", { name: "All" }).getAttribute("aria-selected")).toBe("true");
  });
});
