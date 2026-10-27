"""Run tickets that carry no answer key through the live pipeline and print what it did.

    uv run python -m evals.review --max-cost-inr 30 --calls-per-minute 10

The tickets in `datasets/unlabeled.jsonl` have no expected labels, so nothing here is scored: this
is a spot check for a person to read, not a measurement of recall. For a number, use the
labeled set.
"""

import argparse
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from evals import corpus
from evals.live import (
    CostLimitExceeded,
    LivePipeline,
    PacedClient,
    ProviderUnavailable,
    TicketLive,
)
from evals.metrics import Prediction
from evals.schema import LabeledTicket, Outcome
from services.api.llm.factory import build_llm_client
from services.api.models.common import StrictModel
from services.api.models.draft import DraftOutput
from services.api.models.enums import Intent, Sentiment, Urgency
from services.api.settings import LLMSettings

UNLABELED_PATH = corpus.HERE / "datasets" / "unlabeled.jsonl"


class UnlabeledTicket(StrictModel):
    id: str
    scenario_case: int | None = None
    customer_email: str
    customer_name: str | None = None
    subject: str | None = None
    body: str
    thread_message_count: int = 1


def load_unlabeled(path: Path = UNLABELED_PATH) -> list[LabeledTicket]:
    """The tickets, wrapped so the pipeline can take them. The label fields are placeholders that
    the pipeline never reads; they are not answers."""
    tickets = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = UnlabeledTicket.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(f"{path.name} line {number}: {exc}") from exc
        tickets.append(
            LabeledTicket(
                **row.model_dump(),
                intent=Intent.OTHER,
                urgency=Urgency.NORMAL,
                sentiment=Sentiment.NEUTRAL,
                outcome=Outcome.DRAFT,
            )
        )
    return tickets


def _cell(text: str | None, width: int = 48) -> str:
    text = (text or "").replace("|", "/").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def render(
    tickets: Sequence[LabeledTicket],
    predictions: Sequence[Prediction],
    details: Sequence[TicketLive],
) -> str:
    """A markdown table: what the pipeline decided for each ticket, in the order given."""
    by_id = {d.ticket_id: d for d in details}
    lines = [
        "| Ticket | Subject | Intent | Urgency | Outcome | Reasons | Draft |",
        "|---|---|---|---|---|---|---|",
    ]
    for ticket, prediction in zip(tickets, predictions, strict=True):
        detail = by_id.get(ticket.id)
        draft = detail.drafted if detail and detail.drafted else "none"
        lines.append(
            f"| {ticket.id} | {_cell(ticket.subject)} | {prediction.intent.value} "
            f"| {prediction.urgency} | {prediction.outcome.value} "
            f"| {', '.join(prediction.escalation_reasons) or '-'} | {draft} |"
        )
    return "\n".join(lines)


def render_drafts(tickets: Sequence[LabeledTicket], drafts: Mapping[str, DraftOutput]) -> str:
    """What each customer wrote and what the pipeline would send for a person to approve."""
    blocks = ["## Drafts"]
    for ticket in tickets:
        message = corpus.resolve(ticket.body, ticket.scenario_case)
        lines = [f"### {ticket.id}: {ticket.subject or '(no subject)'}", "", f"> {message}", ""]
        draft = drafts.get(ticket.id)
        if draft is None:
            lines.append("No draft: the ticket was escalated before drafting.")
        else:
            cited = ", ".join(c.source_ref for c in draft.citations) or "none"
            lines += [
                f"Proposed action: {draft.proposed_action.value}. Confidence "
                f"{draft.confidence:.2f}. Cited: {cited}.",
                "",
                draft.reply_text,
            ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.review", description=__doc__.splitlines()[0])
    parser.add_argument("--max-cost-inr", type=float, required=True)
    parser.add_argument("--calls-per-minute", type=float, default=None)
    parser.add_argument("--file", type=Path, default=UNLABELED_PATH)
    parser.add_argument(
        "--show-drafts", action="store_true", help="print each reply under the table"
    )
    parser.add_argument("--save", type=Path, default=None, help="also write the table here")
    args = parser.parse_args(argv)

    try:
        settings = LLMSettings()
    except ValidationError as exc:
        names = sorted({".".join(map(str, e["loc"])) or "settings" for e in exc.errors()})
        print(
            f"The model settings are not valid: {', '.join(names)}. Nothing was run.",
            file=sys.stderr,
        )
        return 2
    if settings.llm_provider == "fake":
        print(
            "LLM_PROVIDER is not set (or is 'fake'): no live model. Nothing was run.",
            file=sys.stderr,
        )
        return 2
    if args.max_cost_inr <= 0:
        print("--max-cost-inr must be above zero. Nothing was run.", file=sys.stderr)
        return 2

    tickets = load_unlabeled(args.file)
    client = build_llm_client(settings)
    if args.calls_per_minute is not None:
        client = PacedClient(client, args.calls_per_minute)
    pipeline = LivePipeline(
        client,
        drafting_model=settings.llm_model or "",
        judge_model=settings.judge_model,
        max_cost_inr=Decimal(str(args.max_cost_inr)),
        progress=lambda line: print(line, file=sys.stderr, flush=True),
    )
    try:
        predictions = [pipeline.predict(t) for t in tickets]
    except CostLimitExceeded as exc:
        print(f"{exc}. Nothing was saved.", file=sys.stderr)
        return 3
    except ProviderUnavailable as exc:
        print(
            f"{exc}. This is usually a rate limit: lower --calls-per-minute or check the quota, "
            "then run again.",
            file=sys.stderr,
        )
        return 4
    finally:
        pipeline.close()

    table = render(tickets, predictions, pipeline.details)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = f"# Unlabeled review, {stamp}\n\nModel: {settings.llm_model}. Not scored.\n\n{table}\n"
    if args.show_drafts:
        out += f"\n{render_drafts(tickets, pipeline.drafts)}\n"
    print(out)
    if args.save:
        args.save.write_text(out, encoding="utf-8")
        print(f"saved to {args.save}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
