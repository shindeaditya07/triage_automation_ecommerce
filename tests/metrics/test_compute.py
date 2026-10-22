"""The dashboard numbers, computed from hand-made rows."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from services.api.metrics.compute import DraftRow, EventRow, TicketRow, compute

DAY = date(2026, 10, 15)  # a Thursday
NOON = datetime(2026, 10, 15, 6, 30, tzinfo=UTC)  # 12:00 IST


def ticket(**over):  # type: ignore[no-untyped-def]
    base = {
        "ticket_id": uuid4(),
        "received_at": NOON,
        "status": "drafted",
        "intent": "refund",
        "urgency": "high",
        "escalation_reason": None,
    }
    return TicketRow(**{**base, **over})


def draft(t: TicketRow, **over):  # type: ignore[no-untyped-def]
    base = {
        "ticket_id": t.ticket_id,
        "attempt": 1,
        "kind": "llm",
        "reply_text": "Hello  there",
        "final_text": None,
        "review_status": "pending",
        "citations": [],
    }
    return DraftRow(**{**base, **over})


def event(t: TicketRow, kind: str, at: datetime, **payload):  # type: ignore[no-untyped-def]
    return EventRow(t.ticket_id, kind, at, payload)


def run(tickets, drafts=(), events=()):  # type: ignore[no-untyped-def]
    return compute(DAY, DAY, list(tickets), list(drafts), list(events))


def test_an_empty_range_has_no_rates_and_every_rule_at_zero() -> None:
    report = run([])
    assert report.tickets_total == 0
    assert report.draft_acceptance.rate is None
    assert report.escalations.rate is None
    assert report.cost.per_ticket_inr is None
    assert report.time_to_draft_seconds.p50 is None
    assert [c.key for c in report.grounding_failures] == [f"G-{n}" for n in range(1, 11)]
    assert all(c.count == 0 for c in report.grounding_failures)


def test_tickets_are_counted_by_intent_and_urgency() -> None:
    report = run(
        [
            ticket(),
            ticket(),
            ticket(intent="return", urgency="low"),
            ticket(intent=None, urgency=None, status="new"),
        ]
    )
    assert {c.key: c.count for c in report.by_intent} == {"refund": 2, "return": 1, "none": 1}
    assert {c.key: c.count for c in report.by_urgency} == {"high": 2, "low": 1, "none": 1}
    refunds = next(g for g in report.intent_urgency if g.intent == "refund")
    assert refunds.by_urgency == {"high": 2}


def test_acceptance_counts_unchanged_text_ignoring_whitespace() -> None:
    a, b, c, pending = (ticket(status="sent") for _ in range(4))
    report = run(
        [a, b, c, pending],
        [
            draft(a, review_status="approved", final_text="Hello there"),  # only whitespace differs
            draft(b, review_status="approved", final_text="Hello there, friend"),  # edited
            draft(c, review_status="approved", final_text=None),  # never edited
            draft(pending),
        ],
    )
    assert (report.draft_acceptance.numerator, report.draft_acceptance.denominator) == (2, 3)
    assert report.draft_acceptance.rate == 0.6667


def test_only_the_latest_attempt_counts_toward_fallback_and_coverage() -> None:
    t = ticket()
    policy = {"source_type": "policy", "source_ref": "RET-1.1"}
    order = {"source_type": "order_field", "source_ref": "orders.order_id"}
    report = run(
        [t],
        [
            draft(t, attempt=1, citations=[policy]),
            draft(t, attempt=3, kind="fallback_template", citations=[order, policy]),
        ],
    )
    assert (report.fallback.numerator, report.fallback.denominator) == (1, 1)
    assert [(c.key, c.count) for c in report.policy_coverage] == [("RET-1.1", 1)]


def test_time_to_draft_and_time_to_send_use_the_right_clocks() -> None:
    t = ticket()
    drafted = NOON + timedelta(minutes=2)
    # 12:02 IST Thursday to 10:30 IST Friday: 6 working hours Thursday + 30 minutes Friday.
    sent = datetime(2026, 10, 16, 5, 0, tzinfo=UTC)
    report = run(
        [t],
        events=[event(t, "ticket.drafted", drafted), event(t, "ticket.mock_sent", sent)],
    )
    assert report.time_to_draft_seconds.p50 == 120
    assert report.time_to_send_business_seconds.p50 == 5 * 3600 + 58 * 60 + 30 * 60


def test_percentiles_use_nearest_rank() -> None:
    tickets = [ticket() for _ in range(20)]
    events = [
        event(t, "ticket.drafted", NOON + timedelta(seconds=10 * (i + 1)))
        for i, t in enumerate(tickets)
    ]
    spread = run(tickets, events=events).time_to_draft_seconds
    assert (spread.samples, spread.p50, spread.p95) == (20, 100, 190)


def test_grounding_failures_are_counted_per_rule_and_unknown_rules_are_ignored() -> None:
    t = ticket()
    report = run(
        [t],
        events=[
            event(t, "draft.attempt_failed", NOON, rule_ids=["G-1", "G-3"]),
            event(t, "draft.attempt_failed", NOON, rule_ids=["G-1", "judge"]),
        ],
    )
    counts = {c.key: c.count for c in report.grounding_failures}
    assert (counts["G-1"], counts["G-3"], counts["G-2"]) == (2, 1, 0)


def test_escalations_split_a_combined_reason() -> None:
    report = run(
        [
            ticket(status="escalated", escalation_reason="legal_threat,prompt_injection"),
            ticket(status="escalated", escalation_reason="llm_error:LLMServerError"),
            ticket(),
        ]
    )
    assert (report.escalations.escalated, report.escalations.total) == (2, 3)
    assert {c.key: c.count for c in report.escalations.by_reason} == {
        "legal_threat": 1,
        "prompt_injection": 1,
        "llm_error:LLMServerError": 1,
    }


def test_cost_sums_every_call_and_averages_over_all_tickets() -> None:
    a, b = ticket(), ticket()
    call = {"cost_inr": "0.25", "input_tokens": 100, "output_tokens": 40}
    report = run(
        [a, b],
        events=[
            event(a, "ticket.classified", NOON, calls=[call]),
            event(a, "ticket.drafted", NOON, calls=[call, call]),
        ],
    )
    assert report.cost.total_inr == 0.75
    assert report.cost.per_ticket_inr == 0.375
    assert (report.cost.input_tokens, report.cost.output_tokens) == (300, 120)
    [day] = report.cost.per_day
    assert (day.day, day.tickets, day.cost_inr) == (DAY, 2, 0.75)
