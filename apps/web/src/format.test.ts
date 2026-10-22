import { describe, expect, it } from "vitest";

import { ageLabel, daysBefore, duration, humanize, istToday, percent, rupees } from "./format";

const NOW = Date.parse("2026-10-22T12:00:00Z");

describe("ageLabel", () => {
  it.each([
    ["2026-10-22T11:59:30Z", "just now"],
    ["2026-10-22T11:55:00Z", "5m ago"],
    ["2026-10-22T10:00:00Z", "2h ago"],
    ["2026-10-19T12:00:00Z", "3d ago"],
    ["2026-10-22T12:05:00Z", "just now"], // clock skew: a time in the future
    ["not a date", "just now"],
  ])("%s -> %s", (received, expected) => {
    expect(ageLabel(received, NOW)).toBe(expected);
  });
});

describe("humanize", () => {
  it("makes a stored value readable", () => {
    expect(humanize("order_status")).toBe("Order status");
    expect(humanize("critical")).toBe("Critical");
  });
});

describe("metric formats", () => {
  it("writes durations in the largest sensible unit", () => {
    expect(duration(null)).toBe("–");
    expect(duration(0.4)).toBe("<1s");
    expect(duration(45)).toBe("45s");
    expect(duration(95)).toBe("1m 35s");
    expect(duration(7260)).toBe("2h 1m");
  });

  it("writes rates and rupees, with a dash for nothing", () => {
    expect(percent(0.6667)).toBe("66.7%");
    expect(percent(null)).toBe("–");
    expect(rupees(0.3125)).toBe("₹0.3125");
    expect(rupees(12.5)).toBe("₹12.50");
    expect(rupees(null)).toBe("–");
  });

  it("counts days back across a month end and reads today in IST", () => {
    expect(daysBefore("2026-11-02", 6)).toBe("2026-10-27");
    // 20:00 UTC on the 21st is already the 22nd in India.
    expect(istToday(new Date("2026-10-21T20:00:00Z"))).toBe("2026-10-22");
  });
});
