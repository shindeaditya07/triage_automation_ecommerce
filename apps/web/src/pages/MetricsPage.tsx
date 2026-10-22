import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useMetrics } from "../api/metrics";
import { BarList, CostLine, StackedBars } from "../components/charts";
import { daysBefore, duration, istToday, percent, rupees } from "../format";
import { metricsCsv } from "../metricsCsv";

type Preset = "7" | "30" | "custom";

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export function MetricsPage() {
  const today = useMemo(() => istToday(), []);
  const [preset, setPreset] = useState<Preset>("30");
  const [custom, setCustom] = useState({ start: daysBefore(today, 29), end: today });
  const range =
    preset === "custom" ? custom : { start: daysBefore(today, Number(preset) - 1), end: today };
  const valid = range.start <= range.end;
  const metrics = useMetrics(valid ? range.start : today, valid ? range.end : today);
  const report = metrics.data;

  return (
    <div className="app">
      <header className="topbar">
        <h1>ReplyDesk</h1>
        <Link to="/">← Back to the queue</Link>
      </header>
      <main className="wide">
        <h2 className="ticket-title">Metrics</h2>
        <div className="range">
          <div role="group" aria-label="Time range" className="tabs">
            {(
              [
                ["7", "Last 7 days"],
                ["30", "Last 30 days"],
                ["custom", "Custom"],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                aria-pressed={preset === id}
                onClick={() => setPreset(id)}
              >
                {label}
              </button>
            ))}
          </div>
          {preset === "custom" && (
            <div className="dates">
              <label>
                From
                <input
                  type="date"
                  value={custom.start}
                  max={today}
                  onChange={(e) => setCustom({ ...custom, start: e.target.value })}
                />
              </label>
              <label>
                To
                <input
                  type="date"
                  value={custom.end}
                  max={today}
                  onChange={(e) => setCustom({ ...custom, end: e.target.value })}
                />
              </label>
            </div>
          )}
          <button
            type="button"
            disabled={!report}
            onClick={() =>
              report &&
              download(`replydesk-metrics-${report.start}-to-${report.end}.csv`, metricsCsv(report))
            }
          >
            Export CSV
          </button>
        </div>
        {!valid && (
          <p className="error" role="alert">
            The start date is after the end date.
          </p>
        )}
        {metrics.isPending && (
          <p className="status" role="status">
            Loading…
          </p>
        )}
        {metrics.isError && (
          <p className="status error" role="alert">
            Could not load the metrics.{" "}
            <button type="button" onClick={() => void metrics.refetch()}>
              Retry
            </button>
          </p>
        )}
        {report && (
          <>
            <p className="hint">
              Days are in IST: {report.start} to {report.end}.
            </p>
            <div className="tiles">
              <Tile label="Tickets" value={String(report.tickets_total)} />
              <Tile
                label="Draft acceptance"
                value={percent(report.draft_acceptance.rate)}
                note={`${report.draft_acceptance.numerator} of ${report.draft_acceptance.denominator} approved drafts sent unchanged`}
              />
              <Tile
                label="Escalation rate"
                value={percent(report.escalations.rate)}
                note={`${report.escalations.escalated} of ${report.escalations.total}`}
              />
              <Tile
                label="Fallback rate"
                value={percent(report.fallback.rate)}
                note={`${report.fallback.numerator} of ${report.fallback.denominator} drafted tickets`}
              />
              <Tile
                label="Time to draft"
                value={duration(report.time_to_draft_seconds.p50)}
                note={`median; 95th percentile ${duration(report.time_to_draft_seconds.p95)}`}
              />
              <Tile
                label="Time to send"
                value={duration(report.time_to_send_business_seconds.p50)}
                note={`median in support hours (Mon–Sat 10:00–18:00 IST); 95th ${duration(report.time_to_send_business_seconds.p95)}`}
              />
              <Tile
                label="Cost per ticket"
                value={rupees(report.cost.per_ticket_inr)}
                note={`${rupees(report.cost.total_inr)} total, ${report.cost.input_tokens + report.cost.output_tokens} tokens`}
              />
            </div>
            <div className="charts">
              <StackedBars title="Tickets by intent and urgency" rows={report.intent_urgency} />
              <BarList
                title="Grounding failures caught, by rule"
                rows={report.grounding_failures}
                empty="No draft was blocked in this range."
              />
              <BarList
                title="Escalations, by reason"
                rows={report.escalations.by_reason}
                empty="No escalations in this range."
              />
              <BarList
                title="Policies cited"
                rows={report.policy_coverage}
                empty="No policy was cited in this range."
              />
              <CostLine days={report.cost.per_day} />
            </div>
          </>
        )}
      </main>
    </div>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <strong className="tile-value">{value}</strong>
      {note && <span className="tile-note">{note}</span>}
    </div>
  );
}
