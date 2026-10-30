import { describe, expect, it } from "vitest";

import type { AuditEvent } from "./api/tickets";
import { describeEvent } from "./auditView";

const event = (event_type: string, payload: Record<string, unknown>): AuditEvent => ({
  event_id: 1,
  event_type,
  actor: "system",
  occurred_at: "2026-10-22T10:00:00Z",
  payload,
});

describe("describeEvent", () => {
  it("explains an escalation with its reason and the provider error", () => {
    const view = describeEvent(
      event("ticket.escalated", {
        reason: "llm_error:LLMServerError",
        stage: "classify",
        error: "HTTP 503",
      }),
    );
    expect(view.label).toBe("Escalated to the owner");
    expect(view.tone).toBe("danger");
    expect(view.lines).toContain("Error: HTTP 503");
  });

  it("explains a blocked draft attempt with its rules and reasons", () => {
    const view = describeEvent(
      event("draft.attempt_failed", {
        attempt: 1,
        outcome: "grounding_failed",
        rule_ids: ["G-1"],
        reasons: ["G-1: order number is not cited"],
      }),
    );
    expect(view.lines).toEqual([
      "Attempt 1: Grounding failed",
      "Rules: G-1",
      "G-1: order number is not cited",
    ]);
  });

  it("sums the cost of the model calls on a draft", () => {
    const view = describeEvent(
      event("ticket.drafted", {
        kind: "llm",
        attempt: 2,
        grounding: { passed: true },
        passages: ["RET-1.1"],
        calls: [{ cost_inr: "0.25" }, { cost_inr: "0.5" }],
      }),
    );
    expect(view.lines).toContain("Grounding passed");
    expect(view.lines).toContain("Policies retrieved: RET-1.1");
    expect(view.lines).toContain("2 model calls, ₹0.7500");
  });

  it("names a retry and where processing resumed", () => {
    const view = describeEvent(event("ticket.retried", { resumed_at: "classified" }));
    expect(view.label).toBe("Owner retried processing");
    expect(view.lines).toEqual(["Resumed at: Classified"]);
  });

  it("shows the owner's decisions with their reasons and diff", () => {
    expect(
      describeEvent(event("ticket.rejected", { reason: "wrong policy cited" })).lines,
    ).toContain("Reason: wrong policy cited");
    const edited = describeEvent(event("draft.edited", { diff: "- old\n+ new" }));
    expect(edited.block).toBe("- old\n+ new");
    const override = describeEvent(
      event("draft.edit_override", { overridden_rules: ["G-1", "G-3"], reason: "customer asked" }),
    );
    expect(override.lines).toEqual(["Warnings overridden: G-1, G-3", "Reason: customer asked"]);
  });

  it("flags an order mismatch and says what the intake cleaned", () => {
    expect(describeEvent(event("ticket.order_lookup", { result: "mismatch" })).tone).toBe("warn");
    const intake = describeEvent(
      event("ticket.ingested", {
        channel: "form",
        pii_masked: 2,
        links_removed: 0,
        hidden_text_removed: true,
      }),
    );
    expect(intake.lines).toEqual([
      "Received by Form",
      "2 personal detail(s) masked",
      "Hidden text removed",
    ]);
  });

  it("falls back to the event name for a type it does not know, and survives an odd payload", () => {
    expect(describeEvent(event("ticket.something_new", {})).label).toBe("Ticket something new");
    expect(
      describeEvent(event("ticket.classified", { classification: "not an object" })).lines,
    ).toEqual(["?, ? urgency"]);
  });
});
