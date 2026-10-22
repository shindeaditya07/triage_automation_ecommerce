import type { MetricsReport } from "./api/metrics";

export const REPORT: MetricsReport = {
  start: "2026-10-16",
  end: "2026-10-22",
  tickets_total: 4,
  by_intent: [{ key: "refund", count: 3 }],
  by_urgency: [{ key: "high", count: 3 }],
  intent_urgency: [{ intent: "refund", by_urgency: { high: 3 } }],
  draft_acceptance: { numerator: 1, denominator: 2, rate: 0.5 },
  time_to_draft_seconds: { samples: 2, p50: 12, p95: 40 },
  time_to_send_business_seconds: { samples: 0, p50: null, p95: null },
  grounding_failures: [
    { key: "G-1", count: 2 },
    { key: "G-2", count: 0 },
  ],
  escalations: {
    escalated: 1,
    total: 4,
    rate: 0.25,
    by_reason: [{ key: "=cmd|calc", count: 1 }],
  },
  fallback: { numerator: 1, denominator: 3, rate: 0.3333 },
  cost: {
    total_inr: 1.5,
    per_ticket_inr: 0.375,
    input_tokens: 900,
    output_tokens: 300,
    per_day: [{ day: "2026-10-22", tickets: 4, cost_inr: 1.5, per_ticket_inr: 0.375 }],
  },
  policy_coverage: [{ key: "RET-1.1", count: 2 }],
};
