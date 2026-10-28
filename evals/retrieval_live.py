"""Measure policy retrieval with the real embedding model next to the fake one.

    uv run python -m evals.retrieval_live --max-cost-inr 5 --calls-per-minute 20

The smoke run embeds with hashed bag-of-words vectors, so its retrieval numbers say nothing about
a real embedding model. This runs the same labeled tickets through `evals.retrieval` twice, once
with the fake client and once with the configured one, and prints both side by side. It embeds the
policy passages once and each drafted ticket once, and stops if spend passes the ceiling.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from decimal import Decimal

from pydantic import ValidationError

from evals import corpus, retrieval
from evals.live import CostLimitExceeded, PacedClient
from evals.metrics import Rate
from evals.retrieval import RetrievalMetrics
from services.api.llm.client import (
    ClassifyRequest,
    DraftRequest,
    EmbedKind,
    JudgeRequest,
    LLMClient,
    LLMResult,
)
from services.api.llm.factory import build_llm_client
from services.api.llm.fake import FakeLLMClient
from services.api.models.draft import DraftOutput
from services.api.models.judge import JudgeOutput
from services.api.models.ticket import Classification
from services.api.settings import LLMSettings


class MeteredClient(LLMClient):
    """Counts embedding calls and their cost, and refuses the next call once spend passes the
    ceiling. Only `embed` is used here; the other calls pass straight through."""

    def __init__(self, inner: LLMClient, max_cost_inr: Decimal) -> None:
        self._inner = inner
        self._max = max_cost_inr
        self.calls = 0
        self.texts = 0
        self.cost_inr = Decimal(0)

    async def classify(self, request: ClassifyRequest) -> LLMResult[Classification]:
        return await self._inner.classify(request)

    async def draft(self, request: DraftRequest) -> LLMResult[DraftOutput]:
        return await self._inner.draft(request)

    async def judge(self, request: JudgeRequest) -> LLMResult[JudgeOutput]:
        return await self._inner.judge(request)

    async def embed(
        self, texts: Sequence[str], kind: EmbedKind = "document"
    ) -> LLMResult[list[list[float]]]:
        if self.cost_inr > self._max:
            raise CostLimitExceeded(self.cost_inr, self._max)
        result = await self._inner.embed(texts, kind)
        self.calls += 1
        self.texts += len(texts)
        self.cost_inr += result.record.cost_inr
        return result

    async def aclose(self) -> None:
        await self._inner.aclose()


def _cell(rate: Rate) -> str:
    return "n/a" if rate.rate is None else f"{rate.rate:.1%} ({rate.successes}/{rate.total})"


def render(fake: RetrievalMetrics, real: RetrievalMetrics, model: str) -> str:
    rows = [
        ("Cited passages retrieved", fake.passages, real.passages),
        ("Tickets with every cited passage", fake.tickets_complete, real.tickets_complete),
        (
            "Cited passages, intent rules only",
            fake.passages_by_intent_only,
            real.passages_by_intent_only,
        ),
    ]
    lines = [f"| measure | fake vectors | {model} |", "| --- | --- | --- |"]
    lines += [f"| {label} | {_cell(a)} | {_cell(b)} |" for label, a, b in rows]
    lines += [
        f"| Passages handed to the draft (mean) | {fake.mean_retrieved:.2f} | "
        f"{real.mean_retrieved:.2f} |",
        f"| Tickets measured | {fake.tickets} | {real.tickets} |",
        "",
        f"Tickets missing a cited passage, fake: {', '.join(fake.missed) or 'none'}",
        f"Tickets missing a cited passage, {model}: {', '.join(real.missed) or 'none'}",
    ]
    return "\n".join(lines)


async def _run(client: MeteredClient) -> tuple[RetrievalMetrics, RetrievalMetrics]:
    """Measure and close the client on the same event loop its connections were opened on."""
    try:
        tickets = corpus.load_labeled()
        fake = await retrieval.measure(tickets, FakeLLMClient())
        real = await retrieval.measure(tickets, client)
        return fake, real
    finally:
        await client.aclose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evals.retrieval_live", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--max-cost-inr", type=float, required=True)
    parser.add_argument("--calls-per-minute", type=float, default=None)
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
            "LLM_PROVIDER is not set (or is 'fake'): no real embeddings. Nothing was run.",
            file=sys.stderr,
        )
        return 2
    if args.max_cost_inr <= 0:
        print("--max-cost-inr must be above zero. Nothing was run.", file=sys.stderr)
        return 2

    inner = build_llm_client(settings)
    if args.calls_per_minute is not None:
        inner = PacedClient(inner, args.calls_per_minute)
    client = MeteredClient(inner, Decimal(str(args.max_cost_inr)))
    try:
        fake, real = asyncio.run(_run(client))
    except CostLimitExceeded as exc:
        print(f"{exc}. Nothing was saved.", file=sys.stderr)
        return 3
    except Exception as exc:  # a provider failure: the numbers would be wrong, so print none
        print(
            f"The embedding call failed ({type(exc).__name__}: {exc}). No result.", file=sys.stderr
        )
        return 4

    print(render(fake, real, settings.embed_model or settings.embed_provider))
    print(
        f"\nEmbedding calls: {client.calls} ({client.texts} texts), "
        f"cost Rs {client.cost_inr:.2f} (0 if EMBED_PRICE_INR_PER_1M is unset).",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
