// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RequireOwner } from "../components/RequireOwner";
import { renderAt, stubApi } from "../test-support";
import { AuditPage } from "./AuditPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const ID = "11111111-1111-4111-8111-111111111111";

const events = (n: number) =>
  Array.from({ length: n }, (_, i) => ({
    event_id: i + 1,
    event_type: i === 0 ? "ticket.ingested" : "ticket.rejected",
    actor: "system",
    occurred_at: "2026-10-22T10:00:00Z",
    payload: i === 0 ? { channel: "form" } : { reason: `reason ${i}` },
  }));

function show(path: string, audit: { status?: number; body?: unknown }) {
  stubApi((_, p) => {
    if (p === "/api/v1/auth/me") return { body: { email: "owner@example.com" } };
    if (p === `/api/v1/tickets/${ID}/audit`) return audit;
    if (p === `/api/v1/tickets/${ID}`) return { body: { subject: "Return my water bottle" } };
    return undefined;
  });
  renderAt(
    path,
    <Route
      path="/audit/:ticketId?"
      element={
        <RequireOwner>
          <AuditPage />
        </RequireOwner>
      }
    />,
  );
}

describe("the audit log page", () => {
  it("asks for a ticket id at first", async () => {
    show("/audit", { body: [] });
    expect(await screen.findByText(/Paste a ticket id/)).toBeTruthy();
  });

  it("lists a ticket's events in words, with the raw record underneath", async () => {
    show(`/audit/${ID}`, { body: events(3) });
    expect(await screen.findByText("Ticket received")).toBeTruthy();
    expect(screen.getByText("Received by Form")).toBeTruthy();
    expect(screen.getByText("Reason: reason 1")).toBeTruthy();
    expect(screen.getByText("Return my water bottle")).toBeTruthy();
    expect(screen.getAllByText("Raw record").length).toBe(3);
  });

  it("pages a long trail", async () => {
    show(`/audit/${ID}`, { body: events(20) });
    expect(await screen.findByText("Page 1 of 2")).toBeTruthy();
    expect(screen.queryByText("Reason: reason 16")).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Reason: reason 16")).toBeTruthy();
    expect(screen.getByText("Page 2 of 2")).toBeTruthy();
  });

  it("says so for an unknown ticket and for something that is not an id", async () => {
    show(`/audit/${ID}`, { status: 404, body: { detail: "no such ticket" } });
    expect(await screen.findByText("There is no ticket with that id.")).toBeTruthy();
    cleanup();
    show("/audit/not-an-id", { body: [] });
    expect(await screen.findByText("That is not a ticket id.")).toBeTruthy();
  });
});
