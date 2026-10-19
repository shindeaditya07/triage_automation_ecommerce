import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from evals import corpus, runner
from evals.metrics import Prediction
from evals.schema import BadDraft, LabeledTicket, Outcome, RecordedPrediction
from services.api.llm.fake import FakeLLMClient, RecordedOutputMissing

DAY = date(2026, 10, 15)


def _run(**overrides: Any) -> runner.Report:
    args: dict[str, Any] = {
        "mode": "smoke",
        "pipelines": runner.smoke_pipelines(),
        "model": "fake-recorded",
        "temperature": 0.0,
        "run_id": "t1",
        "on": DAY,
    }
    return runner.run(**(args | overrides))


@pytest.fixture(autouse=True)
def _no_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "GROUNDING_VALIDATOR", None)


def test_committed_smoke_run_passes_and_says_grounding_was_not_evaluated() -> None:
    report = _run()
    assert report.passed, report.failures
    assert report.grounding.status == "not_evaluated"
    assert report.grounding.metrics is None
    assert "not a pass" in report.grounding.detail
    system, baseline = report.systems
    assert (system.system, system.gated) == ("recorded-fake", True)
    assert (baseline.system, baseline.gated) == ("keyword-baseline", False)
    assert system.escalation.recall.rate is not None
    assert system.escalation.recall.rate >= runner.ESCALATION_RECALL_FLOOR


def test_run_is_deterministic() -> None:
    assert _run().model_dump() == _run().model_dump()


def test_meta_records_model_policy_and_dataset_hashes() -> None:
    meta = _run().meta
    assert meta.model == "fake-recorded" and meta.temperature == 0.0
    assert meta.policy_version == "1.1"
    assert len(meta.policies_sha256) == 64
    assert set(meta.dataset_sha256) == {"labeled.jsonl", "bad_drafts.jsonl"}
    assert (meta.tickets, meta.bad_drafts) == (
        len(corpus.load_labeled()),
        len(corpus.load_bad_drafts()),
    )


def test_low_escalation_recall_fails_the_run() -> None:
    tickets = corpus.load_labeled()

    class Blind:
        name = "blind"
        gated = True

        def predict(self, ticket: LabeledTicket) -> Prediction:
            return Prediction(
                intent=ticket.intent, urgency=ticket.urgency.value, outcome=Outcome.DRAFT
            )

    report = _run(pipelines=[Blind()], tickets=tickets)
    assert not report.passed
    assert any("escalation recall" in f and "blind" in f for f in report.failures)


def test_dataset_problems_fail_the_run_and_skip_scoring() -> None:
    tickets = corpus.load_labeled()[:5]
    report = _run(tickets=tickets)
    assert not report.passed
    assert report.systems == []
    assert any(f.startswith("dataset:") for f in report.failures)


def test_a_registered_validator_is_scored_and_a_miss_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def catches_everything(draft: BadDraft, order: dict[str, object] | None) -> set[str]:
        return set(draft.expected_rules)

    monkeypatch.setattr(runner, "GROUNDING_VALIDATOR", catches_everything)
    good = _run()
    assert good.passed and good.grounding.status == "evaluated"
    assert good.grounding.metrics is not None and good.grounding.metrics.caught.rate == 1.0

    def misses_g8(draft: BadDraft, order: dict[str, object] | None) -> set[str]:
        return set() if "G-8" in draft.expected_rules else set(draft.expected_rules)

    monkeypatch.setattr(runner, "GROUNDING_VALIDATOR", misses_g8)
    bad = _run()
    assert not bad.passed
    assert any(f.startswith("grounding: validator missed") for f in bad.failures)


def test_validator_receives_the_seeded_order(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, dict[str, object] | None] = {}

    def spy(draft: BadDraft, order: dict[str, object] | None) -> set[str]:
        seen[draft.id] = order
        return set(draft.expected_rules)

    monkeypatch.setattr(runner, "GROUNDING_VALIDATOR", spy)
    _run()
    with_order = [d for d in corpus.load_bad_drafts() if d.order_case is not None]
    assert with_order and all(seen[d.id] is not None for d in with_order)
    sample = seen[with_order[0].id]
    assert sample is not None and sample["order_id"].startswith("RD-")  # type: ignore[union-attr]


def test_markdown_report_has_every_section() -> None:
    text = runner.render_markdown(_run())
    for needle in (
        "# Eval report 2026-10-15",
        "## recorded-fake",
        "Escalation recall",
        "| refund |",
        "## Grounding: not_evaluated",
    ):
        assert needle in text


def test_save_writes_report_files_and_refuses_to_overwrite(tmp_path: Path) -> None:
    report = _run()
    target = runner.save(report, tmp_path)
    assert target.name == "2026-10-15-t1"
    assert json.loads((target / "meta.json").read_text())["run_id"] == "t1"
    assert (target / "report.md").read_text().startswith("# Eval report")
    assert json.loads((target / "report.json").read_text())["passed"] is True
    with pytest.raises(FileExistsError):
        runner.save(report, tmp_path)


def test_prompt_hashes_cover_files_and_ignore_dotfiles(tmp_path: Path) -> None:
    assert runner.prompt_hashes(tmp_path / "missing") == {}
    (tmp_path / "classify.md").write_text("one")
    (tmp_path / ".gitkeep").write_text("")
    first = runner.prompt_hashes(tmp_path)
    assert list(first) == ["classify.md"]
    (tmp_path / "classify.md").write_text("two")
    assert runner.prompt_hashes(tmp_path) != first


def test_cli_smoke_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert runner.main(["--mode", "smoke", "--run-id", "cli"], today=lambda: DAY) == 0
    assert "Eval report 2026-10-15 (cli, smoke)" in capsys.readouterr().out
    assert runner.main(["--json"], today=lambda: DAY) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True


def test_cli_full_mode_refuses_without_a_live_client(capsys: pytest.CaptureFixture[str]) -> None:
    assert runner.main(["--mode", "full"]) == 2
    assert "Nothing was run" in capsys.readouterr().err


def test_cli_save_flag_routes_to_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def fake_save(report: runner.Report) -> Path:
        called.append(report.meta.run_id)
        return tmp_path

    monkeypatch.setattr(runner, "save", fake_save)
    assert runner.main(["--save", "--run-id", "x"], today=lambda: DAY) == 0
    assert called == ["x"]


def test_fake_client_returns_recorded_and_fails_closed() -> None:
    recorded = corpus.load_recorded()
    client = FakeLLMClient.from_jsonl(corpus.RECORDED_PATH)
    first: RecordedPrediction = recorded[0]
    result = client.classify(first.ticket_id)
    assert result.intent == first.classification.intent
    assert result.confidence == first.classification.confidence
    with pytest.raises(RecordedOutputMissing, match="T999"):
        client.classify("T999")
    assert client.model == "fake-recorded" and client.temperature == 0.0


def test_fake_client_skips_blank_lines(tmp_path: Path) -> None:
    row = {
        "ticket_id": "T001",
        "classification": {
            "intent": "refund",
            "urgency": "low",
            "confidence": 0.9,
            "sentiment": "neutral",
        },
    }
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps(row) + "\n\n", encoding="utf-8")
    assert FakeLLMClient.from_jsonl(path).classify("T001").flags == []


def test_a_failing_baseline_does_not_fail_the_run_but_a_failing_gated_system_does() -> None:
    report = _run()
    baseline = report.systems[1]
    assert baseline.escalation.recall.rate is not None
    assert baseline.escalation.recall.rate < runner.ESCALATION_RECALL_FLOOR
    assert report.passed

    class Misses:
        name = "weak-gated"
        gated = True

        def predict(self, ticket: LabeledTicket) -> Prediction:
            return Prediction(
                intent=ticket.intent, urgency=ticket.urgency.value, outcome=Outcome.DRAFT
            )

    failing = _run(pipelines=[Misses()])
    assert not failing.passed
    assert any("weak-gated" in f for f in failing.failures)


def test_markdown_compares_the_systems_and_warns_that_the_fixture_column_is_synthetic() -> None:
    text = runner.render_markdown(_run())
    assert "## Comparison with the keyword baseline" in text
    assert "| Metric | recorded-fake | keyword-baseline |" in text
    assert "synthetic fixtures" in text
    assert "comparison only, does not gate the run" in text
