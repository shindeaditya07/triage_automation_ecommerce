import { ApiError } from "./api/client";

/** What the owner is shown for a failed request. The server's own message when it gave one. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) {
      return String((detail as { message: unknown }).message);
    }
    return `The server refused the request (${error.status}).`;
  }
  return "Could not reach the server.";
}

export interface GroundingView {
  passed: boolean | null;
  failures: { ruleId: string; detail: string }[];
  judgeConfidence: number | null;
  judgeError: string | null;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/** The stored grounding verdict, read defensively: it is JSON the pipeline wrote. */
export function readGrounding(raw: Record<string, unknown> | null): GroundingView | null {
  if (!raw) return null;
  const failures: GroundingView["failures"] = [];
  if (Array.isArray(raw.deterministic)) {
    for (const item of raw.deterministic) {
      if (isRecord(item)) {
        failures.push({ ruleId: String(item.rule_id ?? "?"), detail: String(item.detail ?? "") });
      }
    }
  }
  const judge = isRecord(raw.judge) ? raw.judge : null;
  if (judge && Array.isArray(judge.issues)) {
    for (const item of judge.issues) {
      if (isRecord(item)) {
        failures.push({
          ruleId: String(item.rule_id ?? "judge"),
          detail: String(item.detail ?? ""),
        });
      }
    }
  }
  return {
    passed: typeof raw.passed === "boolean" ? raw.passed : null,
    failures,
    judgeConfidence: judge && typeof judge.confidence === "number" ? judge.confidence : null,
    judgeError: typeof raw.judge_error === "string" ? raw.judge_error : null,
  };
}

/** The value of a cited order field ("orders.delivered_at"), or undefined if the snapshot lacks it. */
export function orderValue(
  order: Record<string, unknown> | null,
  sourceRef: string,
): string | undefined {
  if (!order) return undefined;
  let node: unknown = order;
  for (const part of sourceRef.replace(/^orders\./, "").split(".")) {
    if (!isRecord(node) || !(part in node)) return undefined;
    node = node[part];
  }
  if (node === null || node === undefined) return undefined;
  return typeof node === "object" ? JSON.stringify(node) : String(node);
}

/** Order fields worth listing: scalars at the top level. */
export function orderFacts(order: Record<string, unknown> | null): [string, string][] {
  if (!order) return [];
  return Object.entries(order)
    .filter(([, v]) => v !== null && typeof v !== "object")
    .map(([k, v]) => [k, String(v)]);
}
