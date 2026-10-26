"""Turn stored rows into the dashboard numbers. Pure: no database, no clock."""

import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from services.api.grounding.models import RULES
from services.api.metrics.models import (
    Cost,
    CostDay,
    Count,
    Escalations,
    IntentUrgency,
    MetricsReport,
    Rate,
    Spread,
)
from services.api.time_utils import business_seconds, ist_date

NONE = "none"  # the bucket for a ticket with no intent or urgency yet


@dataclass(frozen=True)
class TicketRow:
    ticket_id: object
    received_at: datetime
    status: str
    intent: str | None
    urgency: str | None
    escalation_reason: str | None


@dataclass(frozen=True)
class DraftRow:
    ticket_id: object
    attempt: int
    kind: str
    reply_text: str
    final_text: str | None
    review_status: str
    citations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class EventRow:
    ticket_id: object
    event_type: str
    occurred_at: datetime
    payload: dict[str, Any]


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile of an ascending list (q between 0 and 1)."""
    index = max(math.ceil(q * len(sorted_values)) - 1, 0)
    return sorted_values[index]


def _spread(values: Iterable[float]) -> Spread:
    ordered = sorted(values)
    if not ordered:
        return Spread(samples=0, p50=None, p95=None)
    return Spread(
        samples=len(ordered),
        p50=round(_percentile(ordered, 0.5), 1),
        p95=round(_percentile(ordered, 0.95), 1),
    )


def _rate(numerator: int, denominator: int) -> Rate:
    return Rate(
        numerator=numerator,
        denominator=denominator,
        rate=round(numerator / denominator, 4) if denominator else None,
    )


def _counts(counter: Counter[str]) -> list[Count]:
    return [Count(key=k, count=n) for k, n in sorted(counter.items(), key=lambda i: (-i[1], i[0]))]


def compute(
    start: date,
    end: date,
    tickets: Sequence[TicketRow],
    drafts: Sequence[DraftRow],
    events: Sequence[EventRow],
) -> MetricsReport:
    """`tickets` are those received in the range; `drafts` and `events` are theirs only."""
    received = {t.ticket_id: t.received_at for t in tickets}

    by_intent: Counter[str] = Counter(t.intent or NONE for t in tickets)
    by_urgency: Counter[str] = Counter(t.urgency or NONE for t in tickets)
    grid: dict[str, Counter[str]] = defaultdict(Counter)
    for t in tickets:
        grid[t.intent or NONE][t.urgency or NONE] += 1

    first_event: dict[tuple[object, str], datetime] = {}
    rule_hits: Counter[str] = Counter()
    cost_by_ticket: dict[object, Decimal] = defaultdict(Decimal)
    input_tokens = output_tokens = 0
    for e in sorted(events, key=lambda e: e.occurred_at):
        first_event.setdefault((e.ticket_id, e.event_type), e.occurred_at)
        if e.event_type == "draft.attempt_failed":
            rule_hits.update(r for r in e.payload.get("rule_ids", []) if r in RULES)
        for call in e.payload.get("calls", []):
            cost_by_ticket[e.ticket_id] += Decimal(str(call.get("cost_inr", 0)))
            input_tokens += int(call.get("input_tokens", 0))
            output_tokens += int(call.get("output_tokens", 0))

    to_draft = [
        (at - received[tid]).total_seconds()
        for (tid, kind), at in first_event.items()
        if kind == "ticket.drafted" and tid in received
    ]
    to_send = [
        business_seconds(first_event[(tid, "ticket.drafted")], at)
        for (tid, kind), at in first_event.items()
        if kind == "ticket.mock_sent" and (tid, "ticket.drafted") in first_event
    ]

    latest: dict[object, DraftRow] = {}
    for d in drafts:
        if d.ticket_id not in latest or d.attempt > latest[d.ticket_id].attempt:
            latest[d.ticket_id] = d
    approved = [d for d in latest.values() if d.review_status == "approved"]
    unchanged = sum(
        1 for d in approved if _normalise(d.final_text or d.reply_text) == _normalise(d.reply_text)
    )
    fallback = sum(1 for d in latest.values() if d.kind == "fallback_template")

    coverage: Counter[str] = Counter()
    for d in latest.values():
        coverage.update({c["source_ref"] for c in d.citations if c.get("source_type") == "policy"})

    reasons: Counter[str] = Counter()
    escalated = 0
    for t in tickets:
        if t.status == "escalated":
            escalated += 1
            reasons.update(r for r in (t.escalation_reason or "").split(",") if r)

    per_day_cost: dict[date, Decimal] = defaultdict(Decimal)
    per_day_tickets: Counter[date] = Counter()
    for t in tickets:
        day = ist_date(t.received_at)
        per_day_tickets[day] += 1
        per_day_cost[day] += cost_by_ticket.get(t.ticket_id, Decimal(0))
    total_cost = sum(cost_by_ticket.values(), Decimal(0))

    return MetricsReport(
        start=start,
        end=end,
        tickets_total=len(tickets),
        by_intent=_counts(by_intent),
        by_urgency=_counts(by_urgency),
        intent_urgency=[
            IntentUrgency(intent=i, by_urgency=dict(u))
            for i, u in sorted(grid.items(), key=lambda g: (-sum(g[1].values()), g[0]))
        ],
        draft_acceptance=_rate(unchanged, len(approved)),
        time_to_draft_seconds=_spread(to_draft),
        time_to_send_business_seconds=_spread(to_send),
        grounding_failures=[Count(key=r, count=rule_hits[r]) for r in RULES],
        escalations=Escalations(
            escalated=escalated,
            total=len(tickets),
            rate=round(escalated / len(tickets), 4) if tickets else None,
            by_reason=_counts(reasons),
        ),
        fallback=_rate(fallback, len(latest)),
        cost=Cost(
            total_inr=float(round(total_cost, 4)),
            per_ticket_inr=float(round(total_cost / len(tickets), 4)) if tickets else None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            per_day=[
                CostDay(
                    day=day,
                    tickets=per_day_tickets[day],
                    cost_inr=float(round(per_day_cost[day], 4)),
                    per_ticket_inr=float(round(per_day_cost[day] / per_day_tickets[day], 4)),
                )
                for day in sorted(per_day_tickets)
            ],
        ),
        policy_coverage=_counts(coverage),
    )
