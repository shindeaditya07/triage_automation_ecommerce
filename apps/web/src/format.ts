const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now", "5m ago", "2h ago", "3d ago". A time in the future reads as "just now". */
export function ageLabel(receivedAt: string, now: number = Date.now()): string {
  const elapsed = now - new Date(receivedAt).getTime();
  if (!Number.isFinite(elapsed) || elapsed < MINUTE) return "just now";
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)}m ago`;
  if (elapsed < DAY) return `${Math.floor(elapsed / HOUR)}h ago`;
  return `${Math.floor(elapsed / DAY)}d ago`;
}

/** "order_status" -> "Order status" */
export function humanize(value: string): string {
  const spaced = value.replaceAll("_", " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** 95 -> "1m 35s", 7260 -> "2h 1m", 0.4 -> "<1s". Null (nothing measured) is a dash. */
export function duration(seconds: number | null): string {
  if (seconds === null) return "–";
  if (seconds < 1) return "<1s";
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/** 0.6667 -> "66.7%". Null (nothing to divide) is a dash. */
export function percent(rate: number | null): string {
  return rate === null ? "–" : `${(rate * 100).toFixed(1)}%`;
}

/** "₹1.25" with the paise a per-ticket cost needs. */
export function rupees(amount: number | null): string {
  return amount === null ? "–" : `₹${amount.toFixed(amount < 1 ? 4 : 2)}`;
}

const IST_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" });

/** Today's date in IST as "YYYY-MM-DD". */
export function istToday(now: Date = new Date()): string {
  return IST_DAY.format(now);
}

/** "2026-10-22" minus 6 days is "2026-10-16". */
export function daysBefore(day: string, days: number): string {
  const moment = new Date(`${day}T00:00:00Z`);
  moment.setUTCDate(moment.getUTCDate() - days);
  return moment.toISOString().slice(0, 10);
}
