import type { MetricsReport } from "./api/metrics";

function cell(value: string | number | null): string {
  if (value === null) return "";
  const text = String(value);
  // A leading = + - @ would run as a formula in a spreadsheet, so it is neutralised.
  const safe = /^[=+\-@]/.test(text) && Number.isNaN(Number(text)) ? `'${text}` : text;
  return /[",\n]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
}

/** The dashboard as one CSV: section, name, value. */
export function metricsCsv(report: MetricsReport): string {
  const rows: (string | number | null)[][] = [["section", "name", "value"]];
  const add = (section: string, name: string, value: string | number | null) =>
    rows.push([section, name, value]);

  add("range", "start", report.start);
  add("range", "end", report.end);
  add("tickets", "total", report.tickets_total);
  report.by_intent.forEach((c) => add("tickets_by_intent", c.key, c.count));
  report.by_urgency.forEach((c) => add("tickets_by_urgency", c.key, c.count));
  add("draft_acceptance", "approved_unchanged", report.draft_acceptance.numerator);
  add("draft_acceptance", "approved_total", report.draft_acceptance.denominator);
  add("draft_acceptance", "rate", report.draft_acceptance.rate);
  const draft = report.time_to_draft_seconds;
  add("time_to_draft_seconds", "p50", draft.p50);
  add("time_to_draft_seconds", "p95", draft.p95);
  const send = report.time_to_send_business_seconds;
  add("time_to_send_business_seconds", "p50", send.p50);
  add("time_to_send_business_seconds", "p95", send.p95);
  report.grounding_failures.forEach((c) => add("grounding_failures", c.key, c.count));
  add("escalations", "escalated", report.escalations.escalated);
  add("escalations", "rate", report.escalations.rate);
  report.escalations.by_reason.forEach((c) => add("escalation_reason", c.key, c.count));
  add("fallback", "rate", report.fallback.rate);
  add("cost", "total_inr", report.cost.total_inr);
  add("cost", "per_ticket_inr", report.cost.per_ticket_inr);
  add("cost", "input_tokens", report.cost.input_tokens);
  add("cost", "output_tokens", report.cost.output_tokens);
  report.cost.per_day.forEach((d) => add("cost_per_day_inr", d.day, d.cost_inr));
  report.policy_coverage.forEach((c) => add("policy_cited", c.key, c.count));
  return rows.map((row) => row.map(cell).join(",")).join("\n") + "\n";
}
