from evals import corpus
from evals.baseline import BaselinePipeline
from evals.live import TicketLive
from evals.review import UNLABELED_PATH, load_unlabeled, render


def test_unlabeled_file_loads_and_matches_the_seeded_cases() -> None:
    tickets = load_unlabeled()
    assert len(tickets) == 18
    assert len({t.id for t in tickets}) == len(tickets)
    labeled_ids = {t.id for t in corpus.load_labeled()}
    assert not labeled_ids & {t.id for t in tickets}
    for ticket in tickets:
        if ticket.scenario_case is not None:
            assert ticket.customer_email == corpus.scenario(ticket.scenario_case).customer_email
        assert (
            "{" not in corpus.resolve(ticket.body, ticket.scenario_case)
            or ticket.scenario_case is None
        )


def test_the_file_holds_no_answers() -> None:
    first = UNLABELED_PATH.read_text(encoding="utf-8").splitlines()[0]
    for answer_key in ("intent", "outcome", "escalation_reasons", "policy_ids"):
        assert answer_key not in first


def test_render_has_one_row_per_ticket() -> None:
    tickets = load_unlabeled()[:3]
    predictions = [BaselinePipeline().predict(t) for t in tickets]
    details = [
        TicketLive(ticket_id=tickets[0].id, classified=True, drafted="llm", cost_inr=0, seconds=0)
    ]
    table = render(tickets, predictions, details)
    assert table.count("\n") == 2 + len(tickets) - 1
    assert "| T901 |" in table
    assert "llm" in table.splitlines()[2]
    assert table.splitlines()[3].endswith("| none |")


def test_a_repeat_complaint_carries_its_thread_length() -> None:
    by_id = {t.id: t for t in load_unlabeled()}
    assert by_id["T911"].thread_message_count >= 3
