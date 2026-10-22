import { describe, expect, it } from "vitest";

import { ageLabel, humanize } from "./format";

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
