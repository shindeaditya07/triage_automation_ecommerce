import asyncio
from decimal import Decimal

import pytest

from evals import corpus, retrieval, retrieval_live
from evals.live import CostLimitExceeded
from services.api.llm.fake import FakeLLMClient


def test_metered_client_counts_embedding_calls_and_texts() -> None:
    client = retrieval_live.MeteredClient(FakeLLMClient(), Decimal(1))
    asyncio.run(client.embed(["a", "b"]))
    asyncio.run(client.embed(["c"], "query"))
    assert (client.calls, client.texts) == (2, 3)


def test_each_drafted_ticket_is_embedded_once_after_the_passages() -> None:
    tickets = corpus.load_labeled()
    client = retrieval_live.MeteredClient(FakeLLMClient(), Decimal(1))
    result = asyncio.run(retrieval.measure(tickets, client))
    assert client.calls == 1 + result.tickets


def test_metered_client_stops_once_spend_passes_the_ceiling() -> None:
    client = retrieval_live.MeteredClient(FakeLLMClient(), Decimal(1))
    client.cost_inr = Decimal(2)
    with pytest.raises(CostLimitExceeded):
        asyncio.run(client.embed(["a"]))


def test_render_puts_both_runs_side_by_side() -> None:
    metrics = asyncio.run(retrieval.measure(corpus.load_labeled(), FakeLLMClient()))
    table = retrieval_live.render(metrics, metrics, "gemini-embedding-001")
    assert "| measure | fake vectors | gemini-embedding-001 |" in table
    assert "Cited passages retrieved" in table and "Tickets missing" in table


def test_it_refuses_to_run_without_a_live_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert retrieval_live.main(["--max-cost-inr", "1"]) == 2
    assert "Nothing was run" in capsys.readouterr().err
