/** Reads CSV text into rows of cells: quoted cells may hold commas, quotes ("") and line breaks. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  const source = text.charCodeAt(0) === 0xfeff ? text.slice(1) : text; // a byte-order mark
  for (let i = 0; i < source.length; i++) {
    const ch = source[i]!;
    if (quoted) {
      if (ch === '"' && source[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (ch === '"') {
        quoted = false;
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && source[i + 1] === "\n") i++;
      row.push(cell);
      cell = "";
      rows.push(row);
      row = [];
    } else {
      cell += ch;
    }
  }
  if (cell !== "" || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

export interface Complaint {
  name?: string;
  email: string;
  subject?: string;
  message: string;
  order_id?: string;
}

export const COMPLAINT_COLUMNS = ["name", "email", "order_id", "subject", "message"] as const;

export type ParsedComplaints =
  { ok: true; complaints: Complaint[] } | { ok: false; problem: string };

/** The first row names the columns (any order, any case); `email` and `message` are required. */
export function parseComplaints(text: string): ParsedComplaints {
  const [header, ...body] = parseCsv(text);
  if (!header) return { ok: false, problem: "The file is empty." };
  const names = header.map((h) => h.trim().toLowerCase());
  const unknown = names.filter((n) => !(COMPLAINT_COLUMNS as readonly string[]).includes(n));
  if (unknown.length > 0)
    return {
      ok: false,
      problem: `Unknown column: ${unknown.join(", ")}. Use ${COMPLAINT_COLUMNS.join(", ")}.`,
    };
  for (const required of ["email", "message"]) {
    if (!names.includes(required))
      return {
        ok: false,
        problem: `The file needs ${required === "email" ? "an" : "a"} "${required}" column.`,
      };
  }
  if (body.length === 0) return { ok: false, problem: "The file has a header but no complaints." };
  const complaints = body.map((cells) => {
    const complaint: Record<string, string> = {};
    names.forEach((name, i) => {
      const value = (cells[i] ?? "").trim();
      if (value !== "") complaint[name] = value;
    });
    return complaint as unknown as Complaint;
  });
  return { ok: true, complaints };
}
