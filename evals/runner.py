"""Evaluation runner: `make eval-smoke` (recorded fixtures, no model calls) and `make eval`.

Systems under test are registered by name, so each branch adds a column to the report: the
recorded fake run, the keyword baseline, and later the live model (once classification exists).
Only gated systems can fail the run; the baseline is there for comparison. Nothing here reads
the clock or the network on its own.
"""

import argparse
import hashlib
import sys
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from pydantic import Field

from evals import corpus
from evals.baseline import BaselinePipeline
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
from evals.schema import BadDraft, LabeledTicket, RecordedPrediction
from scripts.policy_lib import POLICIES_PATH, policies_sha256, policies_version
from services.api.llm.fake import FakeLLMClient
from services.api.models.common import StrictModel

ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = Path(__file__).parent / "results"
PROMPTS_DIR = ROOT / "services" / "api" / "prompts"
ESCALATION_RECALL_FLOOR = 0.95

# Set by the grounding branch. Until a validator exists the report says so instead of passing.
GROUNDING_VALIDATOR: GroundingValidator | None = None


class Pipeline(Protocol):
    name: str
    gated: bool  # True: this system must meet the escalation recall floor or the run fails.

    def predict(self, ticket: LabeledTicket) -> Prediction: ...


class RecordedPipeline:
    """Classification from the FakeLLMClient; routing from the recorded outcome."""

    name = "recorded-fake"
    gated = True

    def __init__(self, client: FakeLLMClient, recorded: Sequence[RecordedPrediction]) -> None:
        self._client = client
        self._recorded = {r.ticket_id: r for r in recorded}

    def predict(self, ticket: LabeledTicket) -> Prediction:
        classification = self._client.recorded_classification(ticket.id)
        routing = self._recorded[ticket.id]
        return Prediction(
            intent=classification.intent,
            urgency=classification.urgency.value,
            outcome=routing.outcome,
            escalation_reasons=list(routing.escalation_reasons),
        )


class SystemReport(StrictModel):
    system: str
    gated: bool
    classification: ClassificationMetrics
    escalation: EscalationMetrics


class GroundingReport(StrictModel):
    status: str
    detail: str
    metrics: GroundingMetrics | None = None


class RunMeta(StrictModel):
    run_id: str
    date: str
    mode: str
    model: str
    temperature: float
    prompt_hashes: dict[str, str]
    policy_version: str
    policies_sha256: str
    dataset_sha256: dict[str, str]
    tickets: int
    bad_drafts: int


class Report(StrictModel):
    meta: RunMeta
    problems: list[str] = Field(default_factory=list)
    systems: list[SystemReport] = Field(default_factory=list)
    grounding: GroundingReport
    passed: bool
    failures: list[str] = Field(default_factory=list)


def prompt_hashes(directory: Path = PROMPTS_DIR) -> dict[str, str]:
    """SHA-256 of each prompt file, so a run records exactly which prompts it used."""
    if not directory.is_dir():
        return {}
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
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
    return GroundingReport(
        status="evaluated",
        detail=f"{metrics.caught.successes} of {metrics.caught.total} bad drafts caught",
        metrics=metrics,
    )


def run(
    *,
    mode: str,
    pipelines: Sequence[Pipeline],
    model: str,
    temperature: float,
    run_id: str,
    on: date,
    tickets: Sequence[LabeledTicket] | None = None,
    drafts: Sequence[BadDraft] | None = None,
    recorded: Sequence[RecordedPrediction] | None = None,
) -> Report:
    tickets = list(corpus.load_labeled()) if tickets is None else list(tickets)
    drafts = list(corpus.load_bad_drafts()) if drafts is None else list(drafts)
    recorded = list(corpus.load_recorded()) if recorded is None else list(recorded)

    problems = corpus.validate_datasets(tickets, drafts, recorded)
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
    if grounding.metrics is not None and grounding.metrics.missed:
        failures.append(f"grounding: validator missed {', '.join(grounding.metrics.missed)}")
    return Report(
        meta=meta,
        problems=problems,
        systems=systems,
        grounding=grounding,
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
    lines += [f"## Grounding: {report.grounding.status}", "", report.grounding.detail, ""]
    if report.grounding.metrics is not None:
        g = report.grounding.metrics
        lines += [f"- caught: {_fmt(g.caught)}", f"- right rule reported: {_fmt(g.rule_match)}", ""]
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


def main(
    argv: Sequence[str] | None = None,
    *,
    today: Callable[[], date] = lambda: datetime.now(UTC).date(),
) -> int:
    parser = argparse.ArgumentParser(prog="evals.runner")
    parser.add_argument("--mode", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--save", action="store_true", help="write evals/results/<date>-<run-id>/")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    if args.mode == "full":
        print(
            "make eval needs the classification and escalation steps, which arrive with "
            "feature/p2-classification and feature/p2-pre-lookup-escalation. Nothing was run.",
            file=sys.stderr,
        )
        return 2

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
