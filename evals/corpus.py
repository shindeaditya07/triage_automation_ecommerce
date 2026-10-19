"""Loading and checking the labeled tickets and the seeded bad-draft corpus."""

import importlib
import json
import re
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from evals.schema import BadDraft, LabeledTicket, RecordedPrediction
from scripts.policy_lib import POLICIES_PATH, parse_passages
from services.api.models.enums import Intent, Urgency

HERE = Path(__file__).parent
LABELED_PATH = HERE / "datasets" / "labeled.jsonl"
BAD_DRAFTS_PATH = HERE / "datasets" / "bad_drafts.jsonl"
RECORDED_PATH = HERE / "fixtures" / "recorded_predictions.jsonl"

EVAL_NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
"""The frozen clock the datasets are written against; the mock store seeds relative to it."""
STORE_SIGN_OFF = "Kaveri Home Goods Support"
IST = ZoneInfo("Asia/Kolkata")

LABELED_RANGE = (60, 100)
MIN_BAD_DRAFTS = 30
MIN_BAD_DRAFTS_PER_RULE = 3
MIN_TICKETS_PER_INTENT = 2
REQUIRED_TAGS = frozenset(
    {
        "prompt_injection_body",
        "prompt_injection_subject",
        "prompt_injection_html",
        "delimiter_break",
        "other_customer_order",
        "mixed_intent",
        "vague",
        "angry",
        "missing_order_id",
        "long_input",
        "unicode_tricks",
        "non_english",
    }
)

# Corpus hygiene only: the real lists live in settings.grounding (feature/p3-grounding). These
# keep each bad draft violating the rules it is labeled with, and no others by accident.
_COMMITMENT_MARKERS = (
    "we will refund",
    "we'll replace",
    "you will receive by",
    "we'll deliver by",
    "you'll get a refund",
    "discount code",
)
_APOLOGY_MARKERS = ("sorry", "apologize", "apologise", "apologies", "pardon")
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")
_OVERLONG_CHARS = 2500
_SHORT_CHARS = 1500

T = TypeVar("T", bound=BaseModel)


def _load(path: Path, model: type[T]) -> list[T]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            try:
                rows.append(model.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"{path.name} line {number}: {exc}") from exc
    return rows


def load_labeled(path: Path = LABELED_PATH) -> list[LabeledTicket]:
    return _load(path, LabeledTicket)


def load_bad_drafts(path: Path = BAD_DRAFTS_PATH) -> list[BadDraft]:
    return _load(path, BadDraft)


def load_recorded(path: Path = RECORDED_PATH) -> list[RecordedPrediction]:
    return _load(path, RecordedPrediction)


# --- mock-store orders behind the placeholders ------------------------------------------------


@lru_cache(maxsize=1)
def _seed() -> Any:
    return importlib.import_module("services.mock-store.seed").build_seed(EVAL_NOW)


def scenario(case: int) -> Any:
    return next(s for s in _seed().scenarios if s.case == case)


def order_for_case(case: int | None) -> Any | None:
    if case is None:
        return None
    order_id = scenario(case).order_id
    return next((o for o in _seed().orders if o.order_id == order_id), None)


def _rupees(paise: int) -> str:
    return f"₹{paise // 100:,}" if paise % 100 == 0 else f"₹{paise / 100:,.2f}"


def _day(moment: datetime) -> str:
    local = moment.astimezone(IST)
    return f"{local.day} {local:%B %Y}"


def placeholders(case: int | None) -> dict[str, str]:
    """Values a body or draft may reference, taken from the seeded order for `case`."""
    if case is None:
        return {}
    values = {}
    sc = scenario(case)
    if sc.order_id is not None:
        values["order_id"] = sc.order_id
    order = order_for_case(case)
    if order is not None:
        values["order_value"] = _rupees(order.order_value_paise)
        if order.courier_tracking_number:
            values["tracking"] = order.courier_tracking_number
        if order.delivered_at:
            values["delivered_on"] = _day(order.delivered_at)
        if order.dispatched_at:
            values["dispatched_on"] = _day(order.dispatched_at)
        if order.refund_record:
            values["refund_amount"] = _rupees(order.refund_record.amount_paise)
    return values


def resolve(text: str, case: int | None) -> str:
    for key, value in placeholders(case).items():
        text = text.replace("{" + key + "}", value)
    return text


def order_json_for_case(case: int | None) -> dict[str, object] | None:
    order = order_for_case(case)
    return None if order is None else dict(order.model_dump(mode="json"))


# --- checks -------------------------------------------------------------------------------------


def policy_ids_in_policies_md() -> set[str]:
    return set(parse_passages(POLICIES_PATH.read_text(encoding="utf-8")))


def _ticket_problems(tickets: list[LabeledTicket], known_policies: set[str]) -> list[str]:
    problems = []
    low, high = LABELED_RANGE
    if not low <= len(tickets) <= high:
        problems.append(f"labeled set has {len(tickets)} tickets; the spec asks for {low}-{high}")
    ids = [t.id for t in tickets]
    problems += [f"duplicate ticket id {i}" for i in sorted(set(ids)) if ids.count(i) > 1]

    for ticket in tickets:
        for pid in ticket.policy_ids:
            if pid not in known_policies:
                problems.append(f"{ticket.id}: label cites {pid}, which is not in policies.md")
        text = ticket.body + (ticket.subject or "")
        leftover = _PLACEHOLDER.findall(resolve(text, ticket.scenario_case))
        # Injection tickets may contain brace text of their own; only known keys count.
        known = {"{order_id}", "{tracking}", "{delivered_on}", "{dispatched_on}", "{order_value}"}
        problems += [f"{ticket.id}: unresolved placeholder {p}" for p in leftover if p in known]
        if ticket.scenario_case is not None:
            expected_email = scenario(ticket.scenario_case).customer_email
            if ticket.customer_email != expected_email:
                problems.append(
                    f"{ticket.id}: email {ticket.customer_email} is not case "
                    f"{ticket.scenario_case}'s customer {expected_email}"
                )

    covered = {pid for t in tickets for pid in t.policy_ids}
    problems += [
        f"policy ID {pid} is not referenced by any labeled ticket"
        for pid in sorted(known_policies - covered)
    ]
    cases = {t.scenario_case for t in tickets if t.scenario_case is not None}
    problems += [
        f"mock-store case {n} has no labeled ticket" for n in range(1, 32) if n not in cases
    ]
    for intent in Intent:
        count = sum(1 for t in tickets if t.intent is intent)
        if count < MIN_TICKETS_PER_INTENT:
            problems.append(
                f"intent {intent.value} has {count} tickets; need {MIN_TICKETS_PER_INTENT}"
            )
    problems += [
        f"no ticket has urgency {u.value}"
        for u in Urgency
        if not any(t.urgency is u for t in tickets)
    ]
    tags = {tag for t in tickets for tag in t.tags}
    problems += [
        f"no ticket carries the adversarial tag {tag}" for tag in sorted(REQUIRED_TAGS - tags)
    ]
    return problems


def _apologies(text: str) -> int:
    lowered = text.lower()
    return sum(lowered.count(marker) for marker in _APOLOGY_MARKERS)


def _draft_problems(drafts: list[BadDraft]) -> list[str]:
    problems = []
    if len(drafts) < MIN_BAD_DRAFTS:
        problems.append(
            f"bad-draft corpus has {len(drafts)} drafts; need at least {MIN_BAD_DRAFTS}"
        )
    ids = [d.id for d in drafts]
    problems += [f"duplicate draft id {i}" for i in sorted(set(ids)) if ids.count(i) > 1]
    for rule in (f"G-{n}" for n in range(1, 11)):
        count = sum(1 for d in drafts if rule in d.expected_rules)
        if count < MIN_BAD_DRAFTS_PER_RULE:
            problems.append(f"rule {rule} has {count} bad drafts; need {MIN_BAD_DRAFTS_PER_RULE}")
    for draft in drafts:
        text = resolve(draft.reply_text, draft.order_case)
        leftover = _PLACEHOLDER.search(text)
        if leftover:
            problems.append(f"{draft.id}: unresolved placeholder {leftover.group(0)}")
        rules = set(draft.expected_rules)
        if "G-8" not in rules and not text.rstrip().endswith(STORE_SIGN_OFF):
            problems.append(f"{draft.id}: should end with the sign-off (only G-8 drafts may not)")
        if "G-8" in rules and text.rstrip().endswith(STORE_SIGN_OFF):
            problems.append(f"{draft.id}: labeled G-8 but ends with the exact sign-off")
        if "G-9" in rules and _apologies(text) < 2:
            problems.append(f"{draft.id}: labeled G-9 but has fewer than two apologies")
        if "G-9" not in rules and _apologies(text) > 1:
            problems.append(f"{draft.id}: more than one apology but not labeled G-9")
        if "G-10" in rules and len(text) < _OVERLONG_CHARS:
            problems.append(f"{draft.id}: labeled G-10 but only {len(text)} characters")
        if "G-10" not in rules and len(text) > _SHORT_CHARS:
            problems.append(f"{draft.id}: {len(text)} characters but not labeled G-10")
        has_commitment = any(m in text.lower() for m in _COMMITMENT_MARKERS)
        if "G-3" in rules and not has_commitment:
            problems.append(f"{draft.id}: labeled G-3 but has no commitment phrase")
        if "G-3" not in rules and has_commitment:
            problems.append(f"{draft.id}: has a commitment phrase but is not labeled G-3")
    return problems


def validate_datasets(
    tickets: list[LabeledTicket],
    drafts: list[BadDraft],
    recorded: list[RecordedPrediction],
    known_policies: set[str] | None = None,
) -> list[str]:
    """Every problem found, or an empty list. Nothing here depends on a model or a network."""
    policies = policy_ids_in_policies_md() if known_policies is None else known_policies
    problems = _ticket_problems(tickets, policies) + _draft_problems(drafts)
    ticket_ids = {t.id for t in tickets}
    recorded_ids = [r.ticket_id for r in recorded]
    problems += [f"no recorded prediction for {i}" for i in sorted(ticket_ids - set(recorded_ids))]
    problems += [
        f"recorded prediction for unknown ticket {i}"
        for i in sorted(set(recorded_ids) - ticket_ids)
    ]
    problems += [
        f"duplicate recorded prediction for {i}"
        for i in sorted(set(recorded_ids))
        if recorded_ids.count(i) > 1
    ]
    return problems


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
