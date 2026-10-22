import type { MetricsReport } from "../api/metrics";
import { humanize, rupees } from "../format";

type Counted = { key: string; count: number }[];

/** Horizontal bars with the number beside each: readable without colour, and as a list. */
export function BarList({ title, rows, empty }: { title: string; rows: Counted; empty: string }) {
  const max = Math.max(...rows.map((r) => r.count), 1);
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      {rows.every((r) => r.count === 0) ? (
        <p className="hint">{empty}</p>
      ) : (
        <ul className="bars">
          {rows.map((r) => (
            <li key={r.key}>
              <span className="bar-label">{humanize(r.key)}</span>
              <span className="bar-track" aria-hidden="true">
                <span className="bar-fill" style={{ width: `${(r.count / max) * 100}%` }} />
              </span>
              <span className="bar-value">{r.count}</span>
            </li>
          ))}
        </ul>
      )}
    </figure>
  );
}

const URGENCIES = ["critical", "high", "normal", "low", "none"] as const;

/** One bar per intent, split by urgency. */
export function StackedBars({
  title,
  rows,
}: {
  title: string;
  rows: MetricsReport["intent_urgency"];
}) {
  const totals = rows.map((r) => Object.values(r.by_urgency).reduce((a, b) => a + b, 0));
  const max = Math.max(...totals, 1);
  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      {rows.length === 0 ? (
        <p className="hint">No tickets in this range.</p>
      ) : (
        <>
          <ul className="bars stacked">
            {rows.map((r, i) => (
              <li key={r.intent}>
                <span className="bar-label">{humanize(r.intent)}</span>
                <span className="bar-track" aria-hidden="true">
                  <span className="stack" style={{ width: `${(totals[i]! / max) * 100}%` }}>
                    {URGENCIES.map((u) =>
                      r.by_urgency[u] ? (
                        <span
                          key={u}
                          className={`seg seg-${u}`}
                          style={{ flexGrow: r.by_urgency[u] }}
                        />
                      ) : null,
                    )}
                  </span>
                </span>
                <span className="bar-value">
                  {URGENCIES.filter((u) => r.by_urgency[u])
                    .map((u) => `${r.by_urgency[u]} ${u === "none" ? "unclassified" : u}`)
                    .join(", ")}
                </span>
              </li>
            ))}
          </ul>
          <p className="legend">
            {URGENCIES.map((u) => (
              <span key={u}>
                <span className={`swatch seg-${u}`} aria-hidden="true" />
                {u === "none" ? "Unclassified" : humanize(u)}
              </span>
            ))}
          </p>
        </>
      )}
    </figure>
  );
}

const W = 480;
const H = 140;
const PAD = 24;

/** Cost per ticket for each day, as a line. The values are also listed for screen readers. */
export function CostLine({ days }: { days: MetricsReport["cost"]["per_day"] }) {
  const values = days.map((d) => d.per_ticket_inr ?? 0);
  const max = Math.max(...values, 0.0001);
  const x = (i: number) =>
    PAD + (days.length === 1 ? (W - 2 * PAD) / 2 : (i * (W - 2 * PAD)) / (days.length - 1));
  const y = (v: number) => H - PAD - (v / max) * (H - 2 * PAD);
  return (
    <figure className="chart">
      <figcaption>Cost per ticket, by day</figcaption>
      {days.length === 0 ? (
        <p className="hint">No tickets in this range.</p>
      ) : (
        <>
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cost per ticket by day, as a line">
            <line x1={PAD} y1={H - PAD} x2={W - PAD} y2={H - PAD} className="axis" />
            <polyline
              className="line"
              fill="none"
              points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
            />
            {values.map((v, i) => (
              <circle key={days[i]!.day} cx={x(i)} cy={y(v)} r={3} className="dot" />
            ))}
            <text x={PAD} y={12} className="axis-text">
              {rupees(max)} max
            </text>
          </svg>
          <details>
            <summary>Values by day</summary>
            <ul className="plain">
              {days.map((d) => (
                <li key={d.day}>
                  {d.day}: {rupees(d.per_ticket_inr)} per ticket, {d.tickets} ticket
                  {d.tickets === 1 ? "" : "s"}
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </figure>
  );
}
