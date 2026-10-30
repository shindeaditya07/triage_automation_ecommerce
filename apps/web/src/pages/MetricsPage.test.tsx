// @vitest-environment jsdom
import { cleanup, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RequireOwner } from "../components/RequireOwner";
import { REPORT } from "../metricsFixture";
import { renderAt, stubApi } from "../test-support";
import { MetricsPage } from "./MetricsPage";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function show(report: unknown = REPORT, status = 200) {
  const seen = stubApi((_, path) => {
    if (path === "/api/v1/auth/me") return { body: { email: "owner@example.com" } };
    if (path === "/api/v1/metrics")
      return { status, body: status === 200 ? report : { detail: "x" } };
    return undefined;
  });
  renderAt(
    "/metrics",
    <Route
      path="/metrics"
      element={
        <RequireOwner>
          <MetricsPage />
        </RequireOwner>
      }
    />,
  );
  return seen;
}

describe("the metrics page", () => {
  it("shows the headline figures and the charts", async () => {
    show();
    expect(await screen.findByText("50.0%")).toBeTruthy(); // draft acceptance
    expect(screen.getByText("25.0%")).toBeTruthy(); // escalation rate
    expect(screen.getByText("₹0.3750")).toBeTruthy();
    expect(screen.queryByText("Time to draft")).toBeNull();
    expect(screen.queryByText("Time to send")).toBeNull();
    expect(screen.getByText("Grounding failures caught, by rule")).toBeTruthy();
    expect(screen.getByText("RET-1.1")).toBeTruthy();
    expect(screen.getByText(/3 high/)).toBeTruthy();
  });

  it("asks for the last 30 days at first and the last 7 when told", async () => {
    const seen = show();
    await screen.findByText("50.0%");
    const first = seen.find((r) => r.path === "/api/v1/metrics")!.search;
    const days = (a: string, b: string) => (Date.parse(b) - Date.parse(a)) / 86_400_000;
    expect(days(first.get("start")!, first.get("end")!)).toBe(29);

    await userEvent.setup().click(screen.getByRole("button", { name: "Last 7 days" }));
    await waitFor(() => {
      const last = seen.filter((r) => r.path === "/api/v1/metrics").at(-1)!.search;
      expect(days(last.get("start")!, last.get("end")!)).toBe(6);
    });
  });

  it("offers dates for a custom range", async () => {
    show();
    await screen.findByText("50.0%");
    await userEvent.setup().click(screen.getByRole("button", { name: "Custom" }));
    expect(screen.getByLabelText("From")).toBeTruthy();
    expect(screen.getByLabelText("To")).toBeTruthy();
  });

  it("says so when there is nothing in the range", async () => {
    show({
      ...REPORT,
      tickets_total: 0,
      intent_urgency: [],
      escalations: { escalated: 0, total: 0, rate: null, by_reason: [] },
      policy_coverage: [],
      cost: { ...REPORT.cost, per_ticket_inr: null, per_day: [] },
    });
    expect(await screen.findByText("No escalations in this range.")).toBeTruthy();
    expect(screen.getAllByText("No tickets in this range.").length).toBe(2);
  });

  it("offers a retry when the server fails", async () => {
    show(undefined, 500);
    expect(await screen.findByText(/Could not load the metrics/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });
});
