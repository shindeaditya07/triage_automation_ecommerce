import { describe, expect, it } from "vitest";

import { NO_FILTERS, QUEUE_TABS, queueAddress, readFilters, tabForStatus } from "./queueTabs";

describe("queue addresses", () => {
  it("leaves the default tab and first page out of the address", () => {
    expect(queueAddress(QUEUE_TABS[0]!.id)).toBe("/tickets");
    expect(queueAddress(QUEUE_TABS[0]!.id, 1)).toBe("/tickets");
  });

  it("names the tab and, past the first, the page", () => {
    expect(queueAddress("escalated")).toBe("/tickets?tab=escalated");
    expect(queueAddress("approved", 3)).toBe("/tickets?tab=approved&page=3");
    expect(queueAddress("all", 2)).toBe("/tickets?page=2");
    expect(queueAddress("in-progress")).toBe("/tickets?tab=in-progress");
  });

  it("files every live status under a tab", () => {
    expect(tabForStatus("escalated")?.id).toBe("escalated");
    expect(tabForStatus("sent")?.id).toBe("approved");
    expect(tabForStatus("classified")?.id).toBe("in-progress");
    expect(tabForStatus("new")?.id).toBe("in-progress");
  });

  it("leaves a rejected ticket without a tab, so it shows only under All", () => {
    expect(tabForStatus("rejected")).toBeUndefined();
  });

  it("leads with an All tab that asks for no status filter", () => {
    expect(QUEUE_TABS.map((t) => t.label)).toEqual([
      "All",
      "In progress",
      "Escalated",
      "Approved",
      "Failed",
    ]);
    expect(QUEUE_TABS[0]!.statuses).toEqual([]);
  });

  it("keeps the filters and sort in the address, and leaves the defaults out", () => {
    const filters = { urgency: ["critical", "high"], intent: ["refund"], sort: "urgency" } as const;
    expect(queueAddress("escalated", 2, filters)).toBe(
      "/tickets?tab=escalated&page=2&urgency=critical%2Chigh&intent=refund&sort=urgency",
    );
    expect(queueAddress("all", 1, NO_FILTERS)).toBe("/tickets");
  });

  it("reads filters back, in a fixed order, and ignores what it does not know", () => {
    const params = new URLSearchParams("urgency=high,bogus,critical&intent=x&sort=nope");
    expect(readFilters(params)).toEqual({
      urgency: ["critical", "high"],
      intent: [],
      sort: "newest",
    });
    expect(readFilters(new URLSearchParams("sort=confidence_low")).sort).toBe("confidence_low");
  });
});
