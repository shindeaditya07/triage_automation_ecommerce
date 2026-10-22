import type { AuditEvent } from "./api/tickets";
import { humanize, rupees } from "./format";

export type Tone = "normal" | "ok" | "warn" | "danger";

export interface EventView {
  label: string;
  tone: Tone;
  /** Short lines under the label. */
  lines: string[];
  /** A block of text (an edit diff) shown as written. */
  block?: string;
}

type Payload = Record<string, unknown>;

const str = (v: unknown): string | null => (typeof v === "string" && v !== "" ? v : null);
const num = (v: unknown): number | null => (typeof v === "number" ? v : null);
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : []);
const record = (v: unknown): Payload =>
  typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Payload) : {};

function calls(p: Payload): string | null {
  const all = Array.isArray(p.calls) ? p.calls.map(record) : [];
  if (all.length === 0) return null;
  const cost = all.reduce((sum, c) => sum + Number(c.cost_inr ?? 0), 0);
  return `${all.length} model call${all.length === 1 ? "" : "s"}, ${rupees(cost)}`;
}

/** An audit event in the owner's words. Unknown event types fall back to their name. */
export function describeEvent(event: AuditEvent): EventView {
  const p = record(event.payload);
  const lines: string[] = [];
  const add = (line: string | null | false) => line && lines.push(line);

  switch (event.event_type) {
    case "ticket.ingested": {
      add(`Received by ${humanize(str(p.channel) ?? "unknown channel")}`);
      add((num(p.pii_masked) ?? 0) > 0 && `${p.pii_masked} personal detail(s) masked`);
      add((num(p.links_removed) ?? 0) > 0 && `${p.links_removed} link(s) removed`);
      add(p.hidden_text_removed === true && "Hidden text removed");
      add((num(p.attachments) ?? 0) > 0 && `${p.attachments} attachment(s)`);
      return { label: "Ticket received", tone: "normal", lines };
    }
    case "ticket.classified": {
      const c = record(p.classification);
      add(
        `${humanize(str(c.intent) ?? "?")}, ${str(c.urgency) ?? "?"} urgency` +
          (num(c.confidence) !== null ? `, confidence ${num(c.confidence)!.toFixed(2)}` : ""),
      );
      const flags = list(c.flags);
      add(flags.length > 0 && `Flags: ${flags.map(humanize).join(", ")}`);
      const dropped = list(p.dropped_flags);
      add(
        dropped.length > 0 && `Flags dropped as unsupported: ${dropped.map(humanize).join(", ")}`,
      );
      add(calls(p));
      return { label: "Classified", tone: "normal", lines };
    }
    case "ticket.order_lookup": {
      const result = str(p.result) ?? "unknown";
      add(`Result: ${humanize(result)}`);
      add(str(p.order_id) && `Order ${p.order_id}`);
      const found = list(p.candidates);
      add(found.length > 1 && `${found.length} possible orders`);
      return { label: "Order lookup", tone: result === "mismatch" ? "warn" : "normal", lines };
    }
    case "ticket.escalated": {
      add(`Reason: ${humanize(str(p.reason) ?? "unknown")}`);
      add(str(p.stage) && `Stage: ${humanize(str(p.stage)!)}`);
      add(str(p.error) && `Error: ${p.error}`);
      return { label: "Escalated to the owner", tone: "danger", lines };
    }
    case "ticket.suggestion_only": {
      add(`Reason: ${humanize(str(p.reason) ?? "unknown")}`);
      const triggers = list(p.triggers);
      add(triggers.length > 0 && `Triggers: ${triggers.join(", ")}`);
      return { label: "Marked suggestion only", tone: "warn", lines };
    }
    case "draft.attempt_failed": {
      add(`Attempt ${num(p.attempt) ?? "?"}: ${humanize(str(p.outcome) ?? "failed")}`);
      const rules = list(p.rule_ids);
      add(rules.length > 0 && `Rules: ${rules.join(", ")}`);
      list(p.reasons).forEach((r) => add(r));
      return { label: "Draft attempt blocked", tone: "warn", lines };
    }
    case "draft.fallback_used":
      add("Both AI drafts failed their checks, so the standard reply was used");
      return { label: "Fallback reply used", tone: "warn", lines };
    case "ticket.drafted": {
      const attempt = num(p.attempt);
      add(`${humanize(str(p.kind) ?? "draft")}${attempt === null ? "" : `, attempt ${attempt}`}`);
      const grounding = record(p.grounding);
      if (typeof grounding.passed === "boolean") {
        add(grounding.passed ? "Grounding passed" : "Grounding did not pass");
      }
      const passages = list(p.passages);
      add(passages.length > 0 && `Policies retrieved: ${passages.join(", ")}`);
      add(p.suggestion_only === true && "Suggestion only");
      add(calls(p));
      return { label: "Draft ready for review", tone: "normal", lines };
    }
    case "draft.edited":
      return {
        label: "Owner edited the draft",
        tone: "normal",
        lines,
        block: str(p.diff) ?? undefined,
      };
    case "draft.edit_override": {
      const rules = list(p.overridden_rules);
      add(`Warnings overridden: ${rules.join(", ")}`);
      add(str(p.reason) && `Reason: ${p.reason}`);
      return { label: "Owner saved over grounding warnings", tone: "warn", lines };
    }
    case "ticket.approved":
      add(p.edited === true ? "Sent with the owner's edits" : "Sent as drafted");
      add(p.suggestion_only === true && "Was a suggestion-only draft");
      return {
        label: "Owner approved",
        tone: "ok",
        lines,
        block: p.edited === true ? (str(p.diff) ?? undefined) : undefined,
      };
    case "ticket.rejected":
      add(str(p.reason) && `Reason: ${p.reason}`);
      return { label: "Owner rejected", tone: "danger", lines };
    case "ticket.mock_sent":
      add("Recorded as sent. Nothing left the system.");
      return { label: "Mock send", tone: "ok", lines };
    default:
      return { label: humanize(event.event_type.replace(".", " ")), tone: "normal", lines };
  }
}
