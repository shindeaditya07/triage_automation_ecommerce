"""The live system under test: the real classifier, rules, retrieval, drafter, grounding checks and
judge, with a real model behind them (`make eval`). It costs money, so it refuses to start without
an explicit cost ceiling and stops the run the moment spend passes it.

Orders come from the seeded mock store at the frozen clock and policy embeddings are built in
memory, so a run touches no database. Tickets are processed one at a time on one event loop.
"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from pydantic import Field

from evals import corpus
from evals.metrics import Prediction, Rate, rate
from evals.routing import route
from evals.schema import LabeledTicket, Outcome
from services.api.escalation import post_lookup
from services.api.llm.client import (
    CallRecord,
    ClassifyRequest,
    DraftRequest,
    EmbedKind,
    JudgeRequest,
    LLMClient,
    LLMResult,
)
from services.api.models.common import StrictModel
from services.api.models.draft import DraftOutput
from services.api.models.enums import DraftKind, Intent, TicketStatus, Urgency
from services.api.models.judge import JudgeOutput
from services.api.models.ticket import Classification
from services.api.pipeline.classify import classify_ticket
from services.api.pipeline.draft import (
    GROUNDING_UNAVAILABLE_REASON,
    DraftingConfig,
    DraftStage,
    draft_ticket,
)
from services.api.pipeline.store import TicketText
from services.api.policy_source import parse_passages, policies_path, policies_version
from services.api.retrieval.index import MemoryPolicyIndex

STOP_AFTER_CONSECUTIVE_PROVIDER_FAILURES = 3
PROVIDER_FAILURE_PREFIX = "llm_error:"  # a call failed, as opposed to the model answering badly


def is_provider_failure(reason: str | None) -> bool:
    """A rate limit, outage, timeout, open breaker or rejected request: the model never answered,
    so the ticket says nothing about how good the model is."""
    return reason is not None and (
        reason.startswith(PROVIDER_FAILURE_PREFIX) or reason == GROUNDING_UNAVAILABLE_REASON
    )


class ProviderUnavailable(RuntimeError):
    def __init__(self, reason: str, tickets: int) -> None:
        super().__init__(
            f"the provider failed {STOP_AFTER_CONSECUTIVE_PROVIDER_FAILURES} tickets in a row "
            f"({reason}) after {tickets} tickets; run stopped"
        )


class PacedClient(LLMClient):
    """Spaces model calls at least `60 / calls_per_minute` seconds apart, so a run stays inside a
    rate limit instead of tripping it. Every kind of call counts, embedding included."""

    def __init__(
        self,
        inner: LLMClient,
        calls_per_minute: float,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if calls_per_minute <= 0:
            raise ValueError("calls_per_minute must be above zero")
        self._inner = inner
        self._gap = 60.0 / calls_per_minute
        self._sleep = sleep
        self._monotonic = monotonic
        self._next = 0.0

    async def _wait(self) -> None:
        now = self._monotonic()
        self._next = max(self._next, now) + self._gap
        delay = self._next - self._gap - now
        if delay > 0:
            await self._sleep(delay)

    async def classify(self, request: ClassifyRequest) -> LLMResult[Classification]:
        await self._wait()
        return await self._inner.classify(request)

    async def draft(self, request: DraftRequest) -> LLMResult[DraftOutput]:
        await self._wait()
        return await self._inner.draft(request)

    async def judge(self, request: JudgeRequest) -> LLMResult[JudgeOutput]:
        await self._wait()
        return await self._inner.judge(request)

    async def embed(
        self, texts: Sequence[str], kind: EmbedKind = "document"
    ) -> LLMResult[list[list[float]]]:
        await self._wait()
        return await self._inner.embed(texts, kind)

    async def aclose(self) -> None:
        await self._inner.aclose()


class CostLimitExceeded(RuntimeError):
    def __init__(self, spent: Decimal, limit: Decimal) -> None:
        super().__init__(f"spent Rs {spent:.2f}, over the Rs {limit:.2f} limit; run stopped")
        self.spent, self.limit = spent, limit


class TicketLive(StrictModel):
    """What the live run did for one ticket."""

    ticket_id: str
    classified: bool
    reached_drafting: bool = False
    drafted: str | None = Field(default=None, description="llm, fallback_template, or None")
    attempt: int | None = None
    failed_rules: list[str] = Field(default_factory=list, description="Over every failed attempt.")
    draft_escalation: str | None = Field(
        default=None, description="Why drafting escalated the ticket instead."
    )
    provider_error: str | None = Field(
        default=None, description="The call failed (rate limit, outage); the model never answered."
    )
    cost_inr: float
    seconds: float


class LiveReport(StrictModel):
    drafting_model: str
    judge_model: str | None
    setup_cost_inr: float = Field(description="Embedding the policy passages, once per run.")
    total_cost_inr: float
    cost_per_ticket_inr: float | None
    seconds_p50: float | None
    seconds_p95: float | None
    drafts_attempted: int
    model_draft_first_try: Rate
    model_draft_any_try: Rate
    fallback_used: Rate
    draft_escalated: Rate
    grounding_failures_by_rule: dict[str, int]
    classification_escalated: list[str]
    provider_failures: dict[str, int] = Field(
        default_factory=dict,
        description="Tickets the provider failed, not the model, by reason. Any voids the run.",
    )
    tickets: list[TicketLive]


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _total(calls: Sequence[CallRecord]) -> Decimal:
    return sum((c.cost_inr for c in calls), Decimal(0))


@dataclass
class LivePipeline:
    """A `Pipeline` for the runner. Owns the event loop its client was made on."""

    client: LLMClient
    drafting_model: str
    judge_model: str | None
    max_cost_inr: Decimal
    sign_off: str = corpus.STORE_SIGN_OFF
    name: str = "live"
    gated: bool = True
    progress: Callable[[str], None] | None = None
    _provider_streak: int = 0
    spent: Decimal = Decimal(0)
    setup_cost: Decimal = Decimal(0)
    details: list[TicketLive] = field(default_factory=list)
    _runner: asyncio.Runner = field(default_factory=asyncio.Runner, repr=False)
    _config: DraftingConfig | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.name = f"live ({self.drafting_model})"

    def close(self) -> None:
        self._runner.run(self.client.aclose())
        self._runner.close()

    def _charge(self, amount: Decimal) -> None:
        self.spent += amount
        if self.spent > self.max_cost_inr:
            raise CostLimitExceeded(self.spent, self.max_cost_inr)

    async def _setup(self) -> DraftingConfig:
        if self._config is None:
            text = policies_path().read_text(encoding="utf-8")
            passages = parse_passages(text)
            embedded = await self.client.embed(list(passages.values()))
            vectors = dict(zip(passages, embedded.value, strict=True))
            index = MemoryPolicyIndex(policies_version(text), passages, vectors)
            self.setup_cost = embedded.record.cost_inr
            self._charge(embedded.record.cost_inr)
            self._config = DraftingConfig(index, self.sign_off)
        return self._config

    def predict(self, ticket: LabeledTicket) -> Prediction:
        return self._runner.run(self._predict(ticket))

    async def _predict(self, ticket: LabeledTicket) -> Prediction:
        started = time.monotonic()
        config = await self._setup()
        body = corpus.resolve(ticket.body, ticket.scenario_case)
        stage = await classify_ticket(self.client, ticket.id, ticket.subject, body)
        spent = _total(stage.calls)
        classification = stage.classification
        if classification is None:  # the model failed: production escalates this ticket
            self._record(
                ticket, started, spent, classified=False, escalation=stage.escalation_reason
            )
            return Prediction(
                intent=Intent.OTHER,
                urgency=Urgency.LOW.value,
                outcome=Outcome.NO_DRAFT_ESCALATION,
                escalation_reasons=[stage.escalation_reason or "classification_failed"],
            )
        routed = await route(ticket, classification)
        if routed.route is None or routed.route is post_lookup.Route.NO_DRAFT:
            self._record(ticket, started, spent)
            return routed.prediction
        if routed.lookup is None:  # a draft route always has a lookup
            raise RuntimeError(f"{ticket.id} was routed to drafting with no order lookup")
        text = TicketText(
            ticket_id=_ticket_uuid(ticket.id),
            status=TicketStatus.CLASSIFIED,
            subject=ticket.subject,
            body=body,
            received_at=corpus.EVAL_NOW,
            classification=classification,
            customer_email=ticket.customer_email,
        )
        drafted = await draft_ticket(
            self.client,
            config,
            text,
            routed.lookup,
            suggestion_only=routed.route is post_lookup.Route.SUGGESTION_ONLY,
        )
        spent += _total(drafted.calls)
        self._record(ticket, started, spent, drafted=drafted)
        if drafted.escalation_reason is not None:  # drafting failed: production escalates it
            return routed.prediction.model_copy(
                update={
                    "outcome": Outcome.NO_DRAFT_ESCALATION,
                    "escalation_reasons": [drafted.escalation_reason],
                }
            )
        return routed.prediction

    def _record(
        self,
        ticket: LabeledTicket,
        started: float,
        spent: Decimal,
        *,
        classified: bool = True,
        escalation: str | None = None,
        drafted: DraftStage | None = None,
    ) -> None:
        rules = sorted(
            rule for f in (drafted.failed_attempts if drafted else []) for rule in f.rule_ids
        )
        kind = None
        if drafted is not None and drafted.draft is not None:
            kind = "llm"
        elif drafted is not None and drafted.escalation_reason is None:
            kind = DraftKind.FALLBACK_TEMPLATE.value
        reason = escalation or (drafted.escalation_reason if drafted else None)
        provider_error = reason if is_provider_failure(reason) else None
        self.details.append(
            TicketLive(
                ticket_id=ticket.id,
                classified=classified,
                reached_drafting=drafted is not None,
                drafted=kind,
                attempt=drafted.attempt if drafted and kind else None,
                failed_rules=rules,
                draft_escalation=escalation or (drafted.escalation_reason if drafted else None),
                provider_error=provider_error,
                cost_inr=float(spent),
                seconds=round(time.monotonic() - started, 2),
            )
        )
        self._provider_streak = self._provider_streak + 1 if provider_error else 0
        if self.progress is not None:
            self.progress(
                f"{ticket.id} ({len(self.details)}) "
                f"{'PROVIDER FAILURE ' + provider_error if provider_error else 'ok'}, "
                f"spent Rs {self.spent + spent:.2f}"
            )
        self._charge(spent)
        if provider_error and self._provider_streak >= STOP_AFTER_CONSECUTIVE_PROVIDER_FAILURES:
            raise ProviderUnavailable(provider_error, len(self.details))

    def report(self) -> LiveReport:
        details = self.details
        attempted = [d for d in details if d.reached_drafting]
        drafted = [d for d in details if d.drafted]
        llm = [d for d in drafted if d.drafted == "llm"]
        failures: dict[str, int] = {}
        for d in details:
            for rule in d.failed_rules:
                failures[rule] = failures.get(rule, 0) + 1
        provider_failures: dict[str, int] = {}
        for d in details:
            if d.provider_error:
                provider_failures[d.provider_error] = provider_failures.get(d.provider_error, 0) + 1
        total = sum(d.cost_inr for d in details) + float(self.setup_cost)
        seconds = [d.seconds for d in details]
        return LiveReport(
            drafting_model=self.drafting_model,
            judge_model=self.judge_model,
            setup_cost_inr=float(self.setup_cost),
            total_cost_inr=total,
            cost_per_ticket_inr=total / len(details) if details else None,
            seconds_p50=percentile(seconds, 0.5),
            seconds_p95=percentile(seconds, 0.95),
            drafts_attempted=len(attempted),
            model_draft_first_try=rate(sum(1 for d in llm if d.attempt == 1), len(attempted)),
            model_draft_any_try=rate(len(llm), len(attempted)),
            fallback_used=rate(len(drafted) - len(llm), len(attempted)),
            draft_escalated=rate(len(attempted) - len(drafted), len(attempted)),
            grounding_failures_by_rule=dict(sorted(failures.items())),
            classification_escalated=[d.ticket_id for d in details if not d.classified],
            provider_failures=dict(sorted(provider_failures.items())),
            tickets=details,
        )


def _ticket_uuid(ticket_id: str) -> UUID:
    return UUID(int=int(ticket_id[1:]))
