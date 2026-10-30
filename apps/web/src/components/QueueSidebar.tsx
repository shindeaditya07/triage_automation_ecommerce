import { humanize } from "../format";
import {
  INTENTS,
  SORTS,
  URGENCIES,
  filtersActive,
  type QueueFilters,
  type TicketSort,
} from "../pages/queueTabs";

function toggled<T>(list: readonly T[], value: T, on: boolean): T[] {
  return on ? [...list, value] : list.filter((v) => v !== value);
}

/** The filter and sort controls on the left of the queue. Each change is handed up whole. */
export function QueueSidebar({
  filters,
  onChange,
}: {
  filters: QueueFilters;
  onChange: (next: QueueFilters) => void;
}) {
  return (
    <aside className="queue-sidebar" aria-label="Filter and sort">
      <label className="sort">
        Sort by
        <select
          value={filters.sort}
          onChange={(e) => onChange({ ...filters, sort: e.target.value as TicketSort })}
        >
          {SORTS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      </label>

      <fieldset>
        <legend>Urgency</legend>
        {URGENCIES.map((u) => (
          <label key={u} className="check">
            <input
              type="checkbox"
              checked={filters.urgency.includes(u)}
              onChange={(e) =>
                onChange({ ...filters, urgency: toggled(filters.urgency, u, e.target.checked) })
              }
            />
            {humanize(u)}
          </label>
        ))}
      </fieldset>

      <fieldset>
        <legend>Intent</legend>
        {INTENTS.map((i) => (
          <label key={i} className="check">
            <input
              type="checkbox"
              checked={filters.intent.includes(i)}
              onChange={(e) =>
                onChange({ ...filters, intent: toggled(filters.intent, i, e.target.checked) })
              }
            />
            {humanize(i)}
          </label>
        ))}
      </fieldset>

      {filtersActive(filters) && (
        <button type="button" onClick={() => onChange({ ...filters, urgency: [], intent: [] })}>
          Clear filters
        </button>
      )}
    </aside>
  );
}
