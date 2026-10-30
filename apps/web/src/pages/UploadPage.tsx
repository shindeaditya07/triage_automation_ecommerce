import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { useBulkUpload, type BulkResult } from "../api/tickets";
import { TopBar } from "../components/TopBar";
import { COMPLAINT_COLUMNS, parseComplaints, type Complaint } from "../csv";
import { useTitle } from "../hooks/useTitle";

const CHUNK = 50;
const SHOWN_PROBLEMS = 20;

interface Totals {
  done: number;
  received: number;
  duplicates: number;
  rejected: { row: number; reason: string }[];
}

const NOTHING_YET: Totals = { done: 0, received: 0, duplicates: 0, rejected: [] };

function readText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsText(file);
  });
}

/**
 * The owner uploads a spreadsheet (CSV) of complaints. They are sent in batches of 50 to the same
 * intake the contact form uses. Each batch has its own id, kept until another file is chosen, so
 * pressing the button again after an error never makes a ticket twice.
 */
export function UploadPage() {
  useTitle("Bulk upload");
  const upload = useBulkUpload();
  const input = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [complaints, setComplaints] = useState<Complaint[]>([]);
  const [batchIds, setBatchIds] = useState<string[]>([]);
  const [totals, setTotals] = useState<Totals>(NOTHING_YET);
  const [running, setRunning] = useState(false);
  const [failed, setFailed] = useState(false);

  async function choose(file: File | undefined) {
    setTotals(NOTHING_YET);
    setFailed(false);
    setComplaints([]);
    setProblem(null);
    setFileName(file?.name ?? null);
    if (!file) return;
    let text: string;
    try {
      text = await readText(file);
    } catch {
      setProblem("Could not read that file.");
      return;
    }
    const parsed = parseComplaints(text);
    if (!parsed.ok) {
      setProblem(parsed.problem);
      return;
    }
    setComplaints(parsed.complaints);
    setBatchIds(
      Array.from({ length: Math.ceil(parsed.complaints.length / CHUNK) }, () =>
        crypto.randomUUID(),
      ),
    );
  }

  /** Sends every batch not yet sent, so pressing it again after an error carries on. */
  async function send() {
    setRunning(true);
    setFailed(false);
    let sofar = totals;
    try {
      for (let start = sofar.done; start < complaints.length; start += CHUNK) {
        const result: BulkResult = await upload.mutateAsync({
          batchId: batchIds[start / CHUNK]!,
          complaints: complaints.slice(start, start + CHUNK) as unknown as Record<string, string>[],
        });
        sofar = {
          done: Math.min(start + CHUNK, complaints.length),
          received: sofar.received + result.received,
          duplicates: sofar.duplicates + result.duplicates,
          rejected: [
            ...sofar.rejected,
            ...result.rejected.map((r) => ({ row: start + r.row, reason: r.reason })),
          ],
        };
        setTotals(sofar);
      }
    } catch {
      setFailed(true);
    } finally {
      setRunning(false);
    }
  }

  const finished = complaints.length > 0 && totals.done === complaints.length;

  return (
    <div className="app">
      <TopBar />
      <main id="main" tabIndex={-1} className="upload">
        <h1>Bulk upload</h1>
        <p>
          Upload a CSV file of complaints. Each row is recorded as if the customer had filled in the
          contact form. The first row names the columns: {COMPLAINT_COLUMNS.join(", ")}. Only{" "}
          <code>email</code> and <code>message</code> are required.
        </p>

        <label className="file">
          CSV file
          <input
            ref={input}
            type="file"
            accept=".csv,text/csv"
            disabled={running}
            onChange={(e) => void choose(e.target.files?.[0])}
          />
        </label>

        {problem && (
          <p className="status error" role="alert">
            {problem}
          </p>
        )}

        {complaints.length > 0 && (
          <section aria-label="Ready to upload">
            <p role="status">
              {fileName}: {complaints.length} complaint{complaints.length === 1 ? "" : "s"} ready.
            </p>
            <table className="preview">
              <thead>
                <tr>
                  <th scope="col">Row</th>
                  <th scope="col">Email</th>
                  <th scope="col">Order</th>
                  <th scope="col">Message</th>
                </tr>
              </thead>
              <tbody>
                {complaints.slice(0, 5).map((c, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    <td>{c.email}</td>
                    <td>{c.order_id ?? ""}</td>
                    <td>{c.message.length > 80 ? `${c.message.slice(0, 80)}…` : c.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!finished && (
              <button type="button" disabled={running} onClick={() => void send()}>
                {running
                  ? `Uploading… ${totals.done} of ${complaints.length}`
                  : totals.done > 0
                    ? "Continue upload"
                    : `Upload ${complaints.length} complaint${complaints.length === 1 ? "" : "s"}`}
              </button>
            )}
          </section>
        )}

        {failed && (
          <p className="status error" role="alert">
            The upload stopped at {totals.done} of {complaints.length}. Nothing is lost: press
            Continue upload to carry on.
          </p>
        )}

        {finished && (
          <section aria-label="Result">
            <p className="status" role="status">
              Done. {totals.received} new ticket{totals.received === 1 ? "" : "s"}
              {totals.duplicates > 0 ? `, ${totals.duplicates} already received` : ""}
              {totals.rejected.length > 0 ? `, ${totals.rejected.length} not accepted` : ""}.{" "}
              <Link to="/tickets">Go to the queue</Link>
            </p>
          </section>
        )}

        {totals.rejected.length > 0 && (
          <section aria-label="Rows not accepted">
            <h2>Rows not accepted</h2>
            <ul className="plain">
              {totals.rejected.slice(0, SHOWN_PROBLEMS).map((r) => (
                <li key={r.row}>
                  Row {r.row}: {r.reason}
                </li>
              ))}
            </ul>
            {totals.rejected.length > SHOWN_PROBLEMS && (
              <p>…and {totals.rejected.length - SHOWN_PROBLEMS} more.</p>
            )}
          </section>
        )}
      </main>
    </div>
  );
}
