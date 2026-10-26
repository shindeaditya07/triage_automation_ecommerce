import { describe, expect, it } from "vitest";

import { metricsCsv } from "./metricsCsv";
import { REPORT } from "./metricsFixture";

describe("metricsCsv", () => {
  const lines = metricsCsv(REPORT).trim().split("\n");

  it("has a header and one row per figure", () => {
    expect(lines[0]).toBe("section,name,value");
    expect(lines).toContain("tickets,total,4");
    expect(lines).toContain("grounding_failures,G-1,2");
    expect(lines).toContain("cost,per_ticket_inr,0.375");
    expect(lines).toContain("policy_cited,RET-1.1,2");
  });

  it("leaves a missing figure empty", () => {
    expect(lines).toContain("time_to_send_business_seconds,p50,");
  });

  it("stops a customer-controlled name from running as a spreadsheet formula", () => {
    expect(lines).toContain("escalation_reason,'=cmd|calc,1");
  });
});
