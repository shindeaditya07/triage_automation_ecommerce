"""Evaluation runner: `make eval-smoke` (recorded fixtures, no model calls) and `make eval`.

Systems under test are registered by name, so each branch adds a column to the report: the
recorded fake run, the keyword baseline, and later the live model (once classification exists).
Only gated systems can fail the run; the baseline is there for comparison. Nothing here reads
the clock or the network on its own.
"""

import argparse
import asyncio
import hashlib
import sys
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import Field, ValidationError

from evals import corpus, retrieval
from evals.baseline import BaselinePipeline
from evals.grounding import blocked_good_drafts, failing_rules
from evals.live import (
    CostLimitExceeded,
    LivePipeline,
    LiveReport,
    PacedClient,
    ProviderUnavailable,
)
from evals.metrics import (
    ClassificationMetrics,
    EscalationMetrics,
    GroundingMetrics,
    GroundingValidator,
    Prediction,
    Rate,
    classification_metrics,
    escalation_metrics,
    grounding_metrics,
)
from evals.routing import route
from evals.schema import BadDraft, LabeledTicket, RecordedPrediction
from scripts.policy_lib import POLICIES_PATH, policies_sha256, policies_version
from services.api.llm.factory import build_llm_client
from services.api.llm.fake import FakeLLMClient
from services.api.models.common import StrictModel
from services.api.pipeline.classify import classify_ticket
from services.api.settings import LLMSettings

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).parent / "results"
PROMPTS_DIR = ROOT / "services" / "api" / "prompts"
ESCALATION_RECALL_FLOOR = 0.95
# Reasons produced after the order lookup. The rules compute all of them except the part of
# `outside_policy` no order field shows (a product question the FAQ cannot answer).
POST_LOOKUP_REASONS = frozenset(
    {"order_not_found", "order_mismatch", "repeat_complaint", "high_value", "outside_policy"}
)


# The deterministic validator. Set to None and the report says "not evaluated" instead of passing.
GROUNDING_VALIDATOR: GroundingValidator | None = failing_rules
# Runs the good drafts through it and returns the ones it blocked (false positives).
GOOD_DRAFT_CHECK: Callable[[], dict[str, list[str]]] | None = blocked_good_drafts


class Pipeline(Protocol):
    name: str
    gated: bool  # True: this system must meet the escalation recall floor or the run fails.

    def predict(self, ticket: LabeledTicket) -> Prediction: ...


class RecordedPipeline:
    """The real classification step (recorded client), the real pre-lookup rules, the real order
    lookup against the mock store, and the real post-lookup rules. The steps after (policy
    retrieval, drafting) do not exist yet, so a ticket the rules would draft keeps its recorded
    routing only when that is a reason the rules cannot see."""

    name = "recorded-fake"
    gated = True

    def __init__(self, client: FakeLLMClient, recorded: Sequence[RecordedPrediction]) -> None:
        self._client = client
        self._recorded = {r.ticket_id: r for r in recorded}

    def predict(self, ticket: LabeledTicket) -> Prediction:
        stage = asyncio.run(classify_ticket(self._client, ticket.id, ticket.subject, ticket.body))
        classification = stage.classification
        if classification is None:  # the recorded client does not fail; this is a bug if it does
            raise RuntimeError(
                f"classification of {ticket.id} escalated: {stage.escalation_reason}"
            )
        return asyncio.run(route(ticket, classification)).prediction


class SystemReport(StrictModel):
    system: str
    gated: bool
    classification: ClassificationMetrics
    escalation: EscalationMetrics


class GroundingReport(StrictModel):
    status: str
    detail: str
    metrics: GroundingMetrics | None = None
    false_blocks: dict[str, list[str]] | None = Field(
        default=None, description="Good drafts the validator blocked, with the reasons."
    )


class RetrievalReport(StrictModel):
    status: str
    detail: str
    metrics: retrieval.RetrievalMetrics | None = None


class RunMeta(StrictModel):
    run_id: str
    date: str
    mode: str
    model: str
    temperature: float | None
    prompt_hashes: dict[str, str]
    policy_version: str
    policies_sha256: str
    dataset_sha256: dict[str, str]
    tickets: int
    bad_drafts: int


class Report(StrictModel):
    meta: RunMeta
    live: LiveReport | None = None
    problems: list[str] = Field(default_factory=list)
    systems: list[SystemReport] = Field(default_factory=list)
    grounding: GroundingReport
    retrieval: RetrievalReport
    passed: bool
    failures: list[str] = Field(default_factory=list)


def prompt_hashes(directory: Path = PROMPTS_DIR) -> dict[str, str]:
    """SHA-256 of each prompt file, so a run records exactly which prompts it used."""
    if not directory.is_dir():
        return {}
    return {
        path.name: hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file() and not path.name.startswith(".")
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evaluate(
    tickets: Sequence[LabeledTicket], pipelines: Sequence[Pipeline]
) -> list[SystemReport]:
    reports = []
    for pipeline in pipelines:
        predictions = {t.id: pipeline.predict(t) for t in tickets}
        reports.append(
            SystemReport(
                system=pipeline.name,
                gated=pipeline.gated,
                classification=classification_metrics(tickets, predictions),
                escalation=escalation_metrics(tickets, predictions),
            )
        )
    return reports


def _grounding(drafts: Sequence[BadDraft]) -> GroundingReport:
    if GROUNDING_VALIDATOR is None:
        return GroundingReport(
            status="not_evaluated",
            detail=(
                f"No grounding validator is registered yet, so the {len(drafts)} seeded bad drafts "
                "were loaded and checked for shape but not run through any validator. "
                "This is not a pass."
            ),
        )
    metrics = grounding_metrics(
        drafts,
        GROUNDING_VALIDATOR,
        lambda d: corpus.order_json_for_case(d.order_case),
    )
    false_blocks = GOOD_DRAFT_CHECK() if GOOD_DRAFT_CHECK is not None else None
    good = len(corpus.load_good_drafts())
    detail = f"{metrics.caught.successes} of {metrics.caught.total} bad drafts caught"
    if false_blocks is not None:
        detail += f"; {len(false_blocks)} of {good} good drafts wrongly blocked"
    return GroundingReport(
        status="evaluated",
        detail=detail,
        metrics=metrics,
        false_blocks=false_blocks,
    )


def _retrieval(tickets: Sequence[LabeledTicket]) -> RetrievalReport:
    metrics = retrieval.measure_sync(tickets, FakeLLMClient())
    return RetrievalReport(
        status="evaluated",
        detail=(
            "Embeddings in this run are the fake client's hashed bag-of-words vectors: the "
            "intent-required passages are a real measurement, the vector search is a harness check."
        ),
        metrics=metrics,
    )


def run(
    *,
    mode: str,
    pipelines: Sequence[Pipeline],
    model: str,
    temperature: float | None,
    run_id: str,
    on: date,
    tickets: Sequence[LabeledTicket] | None = None,
    drafts: Sequence[BadDraft] | None = None,
    recorded: Sequence[RecordedPrediction] | None = None,
    live: Callable[[], LiveReport] | None = None,
    trial: bool = False,
) -> Report:
    tickets = list(corpus.load_labeled()) if tickets is None else list(tickets)
    drafts = list(corpus.load_bad_drafts()) if drafts is None else list(drafts)
    recorded = list(corpus.load_recorded()) if recorded is None else list(recorded)

    # A trial runs a few tickets, but the whole dataset must still be sound.
    whole = list(corpus.load_labeled()) if trial else tickets
    problems = corpus.validate_datasets(whole, drafts, recorded)
    meta = RunMeta(
        run_id=run_id,
        date=on.isoformat(),
        mode=mode,
        model=model,
        temperature=temperature,
        prompt_hashes=prompt_hashes(),
        policy_version=policies_version(POLICIES_PATH.read_text(encoding="utf-8")),
        policies_sha256=policies_sha256(),
        dataset_sha256={
            "labeled.jsonl": _sha(corpus.LABELED_PATH),
            "bad_drafts.jsonl": _sha(corpus.BAD_DRAFTS_PATH),
        },
        tickets=len(tickets),
        bad_drafts=len(drafts),
    )
    failures = [f"dataset: {p}" for p in problems]
    systems: list[SystemReport] = []
    if not problems:
        systems = _evaluate(tickets, pipelines)
        for system in (s for s in systems if s.gated):
            value = system.escalation.recall.rate
            if value is None or value < ESCALATION_RECALL_FLOOR:
                failures.append(
                    f"{system.system}: escalation recall {value} is below {ESCALATION_RECALL_FLOOR}"
                )
    grounding = _grounding(drafts)
    retrieval_report = (
        _retrieval(tickets)
        if not problems
        else RetrievalReport(status="not_evaluated", detail="the datasets have problems")
    )
    live_report = live() if live is not None and not problems else None
    if live_report is not None and live_report.provider_failures:
        count = sum(live_report.provider_failures.values())
        failures.append(
            f"live: the provider failed {count} tickets ({live_report.provider_failures}), so "
            "the live numbers are not valid. Lower --calls-per-minute and run again"
        )
    if grounding.metrics is not None and grounding.metrics.missed:
        failures.append(f"grounding: validator missed {', '.join(grounding.metrics.missed)}")
    if grounding.false_blocks:
        failures.append(
            f"grounding: good drafts blocked: {', '.join(sorted(grounding.false_blocks))}"
        )
    return Report(
        meta=meta,
        live=live_report,
        problems=problems,
        systems=systems,
        grounding=grounding,
        retrieval=retrieval_report,
        passed=not failures,
        failures=failures,
    )


# --- rendering ----------------------------------------------------------------------------------


def _fmt(value: Rate | None) -> str:
    if value is None or value.rate is None or value.low is None or value.high is None:
        return "n/a"
    return f"{value.rate:.3f} ({value.low:.2f}-{value.high:.2f}) n={value.total}"


def _comparison(systems: Sequence[SystemReport], mode: str) -> list[str]:
    rows: list[tuple[str, Callable[[SystemReport], Rate]]] = [
        ("Intent accuracy", lambda s: s.classification.accuracy),
        ("Urgency accuracy", lambda s: s.classification.urgency_accuracy),
        ("Escalation recall", lambda s: s.escalation.recall),
        ("Escalation precision", lambda s: s.escalation.precision),
        ("No-draft escalation recall", lambda s: s.escalation.no_draft_recall),
        ("Outcome accuracy", lambda s: s.escalation.outcome_accuracy),
    ]
    lines = [
        "## Comparison with the keyword baseline",
        "",
        *(
            [
                "Smoke mode: the recorded-fake column replays synthetic fixtures with seeded "
                "mistakes, so it measures the harness, not a model. Only the keyword-baseline "
                "column is a real measurement.",
                "",
            ]
            if mode == "smoke"
            else []
        ),
        "| Metric | " + " | ".join(s.system for s in systems) + " |",
        "|---|" + "---|" * len(systems),
    ]
    for label, getter in rows:
        lines.append(f"| {label} | " + " | ".join(_fmt(getter(s)) for s in systems) + " |")
    return [*lines, ""]


def _render_live(live: LiveReport) -> list[str]:
    lines = [
        "## Live run",
        "",
        f"- drafter `{live.drafting_model}`, judge `{live.judge_model}`",
        f"- tickets that reached drafting: {live.drafts_attempted}",
        f"- model draft passed grounding on the first try: {_fmt(live.model_draft_first_try)}",
        f"- model draft passed grounding on either try: {_fmt(live.model_draft_any_try)}",
        f"- fell back to the standard template: {_fmt(live.fallback_used)}",
        f"- drafting escalated the ticket instead: {_fmt(live.draft_escalated)}",
        "- grounding failures by rule (every failed attempt): "
        + (", ".join(f"{k} x{v}" for k, v in live.grounding_failures_by_rule.items()) or "none"),
        f"- classification failed, escalated: {', '.join(live.classification_escalated) or 'none'}",
        "- provider failures (the call failed, not the model; any makes the run INVALID): "
        + (", ".join(f"{k} x{v}" for k, v in live.provider_failures.items()) or "none"),
        f"- cost: Rs {live.total_cost_inr:.2f} in total (Rs {live.setup_cost_inr:.2f} to embed "
        f"the policy), "
        + (
            f"Rs {live.cost_per_ticket_inr:.3f} per ticket"
            if live.cost_per_ticket_inr is not None
            else "no tickets"
        ),
        f"- seconds per ticket: p50 {live.seconds_p50}, p95 {live.seconds_p95}",
        "",
    ]
    return lines


def render_markdown(report: Report) -> str:
    meta = report.meta
    lines = [
        f"# Eval report {meta.date} ({meta.run_id}, {meta.mode})",
        "",
        f"- model: `{meta.model}`, temperature {meta.temperature}",
        f"- policy version {meta.policy_version}, policies.md sha256 `{meta.policies_sha256[:12]}`",
        f"- {meta.tickets} labeled tickets, {meta.bad_drafts} seeded bad drafts",
        f"- prompt hashes: {meta.prompt_hashes or 'none yet (no prompt files exist)'}",
        f"- grounding validator: {report.grounding.status}",
        f"- result: **{'PASS' if report.passed else 'FAIL'}**",
        "",
    ]
    for failure in report.failures:
        lines.append(f"- FAILURE: {failure}")
    if len(report.systems) > 1:
        lines += _comparison(report.systems, meta.mode)
    for system in report.systems:
        esc = system.escalation
        label = "" if system.gated else " (comparison only, does not gate the run)"
        lines += [
            f"## {system.system}{label}",
            "",
            "| Metric | Value (95% Wilson) |",
            "|---|---|",
            f"| Intent accuracy | {_fmt(system.classification.accuracy)} |",
            f"| Urgency accuracy | {_fmt(system.classification.urgency_accuracy)} |",
            f"| Escalation recall | {_fmt(esc.recall)} |",
            f"| Escalation precision | {_fmt(esc.precision)} |",
            f"| No-draft escalation recall | {_fmt(esc.no_draft_recall)} |",
            f"| Outcome accuracy | {_fmt(esc.outcome_accuracy)} |",
            "",
            f"Missed escalations: {', '.join(esc.missed) or 'none'}. "
            f"False escalations: {', '.join(esc.false_escalations) or 'none'}.",
            "",
            "| Intent | Support | Precision | Recall |",
            "|---|---|---|---|",
        ]
        for item in system.classification.per_intent:
            lines.append(
                f"| {item.intent.value} | {item.support} | {_fmt(item.precision)} | "
                f"{_fmt(item.recall)} |"
            )
        lines.append("")
    if report.live is not None:
        lines += _render_live(report.live)
    lines += [f"## Retrieval: {report.retrieval.status}", "", report.retrieval.detail, ""]
    if report.retrieval.metrics is not None:
        r = report.retrieval.metrics
        lines += [
            f"- tickets measured: {r.tickets} (drafted or suggestion-only, with cited passages)",
            f"- cited passages retrieved: {_fmt(r.passages)}",
            f"- of those, by intent alone: {_fmt(r.passages_by_intent_only)}",
            f"- tickets with every cited passage: {_fmt(r.tickets_complete)}",
            f"- mean passages per ticket: {r.mean_retrieved:.1f}",
            f"- tickets missing a cited passage: {', '.join(r.missed) or 'none'}",
            "",
        ]
    lines += [f"## Grounding: {report.grounding.status}", "", report.grounding.detail, ""]
    if report.grounding.metrics is not None:
        g = report.grounding.metrics
        lines += [f"- caught: {_fmt(g.caught)}", f"- right rule reported: {_fmt(g.rule_match)}"]
        if report.grounding.false_blocks is not None:
            blocked = ", ".join(sorted(report.grounding.false_blocks)) or "none"
            lines.append(f"- good drafts wrongly blocked: {blocked}")
        lines.append("")
    return "\n".join(lines)


def save(report: Report, base: Path = RESULTS_DIR) -> Path:
    target = base / f"{report.meta.date}-{report.meta.run_id}"
    target.mkdir(parents=True, exist_ok=False)
    (target / "meta.json").write_text(
        report.meta.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (target / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (target / "report.md").write_text(render_markdown(report), encoding="utf-8")
    return target


# --- command line -------------------------------------------------------------------------------


def smoke_pipelines() -> list[Pipeline]:
    recorded = corpus.load_recorded()
    client = FakeLLMClient.from_jsonl(corpus.RECORDED_PATH)
    return [RecordedPipeline(client, recorded), BaselinePipeline()]


def _full(args: argparse.Namespace, today: Callable[[], date]) -> int:
    """`make eval`: the whole pipeline with a live model. Refuses to start unless it can also
    measure what it spends, because the cost ceiling is the only thing that bounds the bill."""
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
            "LLM_PROVIDER is not set (or is 'fake'), so there is no live model to evaluate. "
            "Set the LLM_*, JUDGE_* and EMBED_* variables (see .env.example). Nothing was run.",
            file=sys.stderr,
        )
        return 2
    prices = (settings.llm_input_price_inr_per_1m, settings.llm_output_price_inr_per_1m)
    if not all(prices) or not all(
        (settings.judge_input_price_inr_per_1m, settings.judge_output_price_inr_per_1m)
    ):
        print(
            "The *_PRICE_INR_PER_1M settings are zero, so spend would read as Rs 0 and no cost "
            "limit could ever stop the run. Fill them in from the provider price pages. "
            "Nothing was run.",
            file=sys.stderr,
        )
        return 2
    if args.max_cost_inr is None or args.max_cost_inr <= 0:
        print(
            "Pass --max-cost-inr N (rupees). The run stops if spend passes it. Nothing was run.",
            file=sys.stderr,
        )
        return 2

    client = build_llm_client(settings)
    if args.calls_per_minute is not None:
        client = PacedClient(client, args.calls_per_minute)
    pipeline = LivePipeline(
        client,
        drafting_model=settings.llm_model or "",
        judge_model=settings.judge_model,
        max_cost_inr=args.max_cost_inr,
        progress=lambda line: print(line, file=sys.stderr, flush=True),
    )
    tickets = list(corpus.load_labeled())
    trial = args.limit is not None
    if trial:
        tickets = tickets[: args.limit]
    run_id = args.run_id or f"{'trial' if trial else 'full'}-{uuid.uuid4().hex[:8]}"
    try:
        report = run(
            mode="full",
            pipelines=[pipeline, BaselinePipeline()],
            model=f"{settings.llm_model} (judge {settings.judge_model})",
            temperature=settings.llm_temperature,
            run_id=run_id,
            on=today(),
            tickets=tickets,
            live=pipeline.report,
            trial=trial,
        )
    except CostLimitExceeded as exc:
        print(f"{exc}. Nothing was saved.", file=sys.stderr)
        return 3
    except ProviderUnavailable as exc:
        print(
            f"{exc}. Nothing was saved. This is usually a rate limit: lower --calls-per-minute "
            "or check the key's quota, then run again.",
            file=sys.stderr,
        )
        return 4
    finally:
        pipeline.close()
    print(report.model_dump_json(indent=2) if args.json else render_markdown(report))
    if args.save:
        print(f"saved to {save(report)}", file=sys.stderr)
    return 0 if report.passed else 1


def main(
    argv: Sequence[str] | None = None,
    *,
    today: Callable[[], date] = lambda: datetime.now(UTC).date(),
) -> int:
    parser = argparse.ArgumentParser(prog="evals.runner")
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--save", action="store_true", help="write evals/results/<date>-<run-id>/")
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--max-cost-inr",
        type=Decimal,
        default=None,
        help="full mode: stop the run if spend passes this many rupees (required)",
    )
    parser.add_argument(
        "--calls-per-minute",
        type=float,
        default=None,
        help="full mode: space model calls to stay inside the provider's rate limit",
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="full mode: run only the first N tickets (a trial)"
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    if args.mode == "full":
        return _full(args, today)

    run_id = args.run_id or f"smoke-{uuid.uuid4().hex[:8]}"
    report = run(
        mode="smoke",
        pipelines=smoke_pipelines(),
        model=FakeLLMClient.model,
        temperature=FakeLLMClient.temperature,
        run_id=run_id,
        on=today(),
    )
    print(report.model_dump_json(indent=2) if args.json else render_markdown(report))
    if args.save:
        print(f"saved to {save(report)}", file=sys.stderr)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
