"""The deterministic grounding rules G-1 to G-10 (spec section 13.1).

`validate` is a pure function. Each rule is its own function returning `RuleFailure`s, so each can
be tested alone. The rules fail closed: a value they cannot place is a failure, not a pass. What
they cannot see (a claim phrased in words no list anticipates) is left to the judge; see
docs/grounding.md.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from services.api.escalation.injection import scan_injection
from services.api.grounding import extract
from services.api.grounding.config import GroundingConfig, default_config
from services.api.grounding.extract import Duration
from services.api.grounding.models import RuleFailure, ValidationResult
from services.api.grounding.order_index import OrderIndex, covered, normalise_ref
from services.api.models.draft import Citation
from services.api.models.enums import SourceType

ECHO_WORDS = 5  # a run of this many words repeated from an injected instruction is an echo


@dataclass(frozen=True)
class GroundingContext:
    """What the reply was written from, and who it is for."""

    order: dict[str, Any] | None  # the order snapshot as JSON, None when no order was found
    customer_email: str  # the sender
    policy_texts: Mapping[str, str]  # policy ID -> passage text: the loaded policy set
    sign_off: str
    customer_message: str | None = None  # subject and body, when known (G-5, G-6, G-7)


@dataclass
class _Run:
    reply: str
    citations: Sequence[Citation]
    context: GroundingContext
    config: GroundingConfig
    order_index: OrderIndex | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.context.order is not None:
            self.order_index = OrderIndex.build(self.context.order)

    @cached_property
    def text(self) -> str:
        return extract.normalise(self.reply)

    @cached_property
    def lower(self) -> str:
        return self.text.lower()

    @cached_property
    def cited_policy_ids(self) -> set[str]:
        return {c.source_ref.strip() for c in self.citations if c.source_type is SourceType.POLICY}

    @cached_property
    def cited_fields(self) -> set[str]:
        return {
            normalise_ref(c.source_ref)
            for c in self.citations
            if c.source_type is SourceType.ORDER_FIELD
        }

    @cached_property
    def message_lower(self) -> str:
        return extract.normalise(self.context.customer_message or "").lower()

    @cached_property
    def cited_policy_text(self) -> str:
        texts = self.context.policy_texts
        return " ".join(texts[pid] for pid in sorted(self.cited_policy_ids) if pid in texts)

    @cached_property
    def sentences(self) -> list[str]:
        return extract.sentences(self.text)

    @cached_property
    def order_id_values(self) -> list[str]:
        return extract.matches_of(self.config.compiled("order_id_patterns"), self.text)

    @cached_property
    def message_order_ids(self) -> list[str]:
        return extract.matches_of(self.config.compiled("order_id_patterns"), self.message_lower)

    @cached_property
    def allowed_text(self) -> str:
        """What a claim may legitimately draw on: the order, the customer's message, the cited
        policy and the store's own sign-off."""
        parts = [
            self.order_index.text if self.order_index else "",
            self.message_lower,
            extract.normalise(self.cited_policy_text).lower(),
            self.context.sign_off.lower(),
        ]
        return " ".join(parts)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text))


def _fail(rule: str, span: str, detail: str, *, citation_missing: bool = False) -> RuleFailure:
    return RuleFailure(
        rule_id=rule, span=span[:80], detail=detail, citation_missing=citation_missing
    )


def _cite_hint(paths: Iterable[str]) -> str:
    """Tells the drafter which field to cite, so a retry can fix a missing citation."""
    return "; cite " + " or ".join(f"orders.{path}" for path in sorted(paths)[:3])


def _policy_values(texts: Sequence[str]) -> tuple[set[int], set[Duration]]:
    amounts: set[int] = set()
    durations: set[Duration] = set()
    for text in texts:
        amounts.update(found.value for found in extract.amounts(text))
        durations.update(found.value for found in extract.durations(text))
    return amounts, durations


# --- G-1 -----------------------------------------------------------------------------------


def check_order_values(run: _Run) -> list[RuleFailure]:
    """G-1: every amount, date, order number and tracking number taken from the order must match
    a field the reply cites."""
    failures: list[RuleFailure] = []
    index = run.order_index
    cited = run.cited_fields
    policy_amounts, _ = _policy_values([run.cited_policy_text])
    all_policy_amounts, _ = _policy_values(list(run.context.policy_texts.values()))

    for sentence in run.sentences:
        policy_context = bool(extract.policy_ids(sentence)) or "policy" in sentence.lower()
        for amount in extract.amounts(sentence):
            paise, span = amount.value, amount.span
            if paise in policy_amounts or (index and covered(cited, index.amount_paths(paise))):
                continue
            in_order = bool(index and index.amount_paths(paise))
            if policy_context or (paise in all_policy_amounts and not in_order):
                continue  # judged by G-2
            if in_order:
                failures.append(
                    _fail(
                        "G-1",
                        span,
                        "amount from the order is not cited"
                        + _cite_hint(index.amount_paths(paise) if index else ()),
                        citation_missing=True,
                    )
                )
            else:
                failures.append(_fail("G-1", span, "amount matches no field of the order"))

    for dated in extract.dates(run.text):
        day, span = dated.value, dated.span
        if index is None:
            failures.append(_fail("G-1", span, "a date with no order record to cite"))
        elif covered(cited, index.date_paths(day)):
            continue
        elif index.date_paths(day):
            failures.append(
                _fail(
                    "G-1",
                    span,
                    "date from the order is not cited" + _cite_hint(index.date_paths(day)),
                    citation_missing=True,
                )
            )
        else:
            failures.append(_fail("G-1", span, "date matches no field of the order"))

    failures += _check_identifiers(run)
    return failures


def _check_identifiers(run: _Run) -> list[RuleFailure]:
    failures: list[RuleFailure] = []
    index = run.order_index
    ids = run.order_id_values
    tracking = extract.matches_of(run.config.compiled("tracking_patterns"), run.text)
    for token in ids + tracking:
        paths = index.token_paths(token) if index else set()
        if paths and covered(run.cited_fields, paths):
            continue
        if paths:
            failures.append(
                _fail(
                    "G-1",
                    token,
                    "identifier from the order is not cited" + _cite_hint(paths),
                    citation_missing=True,
                )
            )
        elif token in ids and token in run.message_order_ids:
            continue  # the customer's own words, echoed
        elif token in ids:
            failures.append(_fail("G-1", token, "order number does not match the order record"))
        else:
            failures.append(_fail("G-1", token, "tracking number does not match the order record"))
    return failures


# --- G-2 -----------------------------------------------------------------------------------


def check_policy_values(run: _Run) -> list[RuleFailure]:
    """G-2: every duration, and every amount said to come from policy, must be in a cited
    passage."""
    failures: list[RuleFailure] = []
    index = run.order_index
    cited_amounts, cited_durations = _policy_values([run.cited_policy_text])
    all_amounts, all_durations = _policy_values(list(run.context.policy_texts.values()))

    for sentence in run.sentences:
        policy_context = bool(extract.policy_ids(sentence)) or "policy" in sentence.lower()
        for found in extract.durations(sentence):
            if found.value in cited_durations:
                continue
            in_policy = found.value in all_durations
            detail = (
                "duration is in a policy passage the reply does not cite"
                if in_policy
                else ("duration is not in any cited policy passage")
            )
            failures.append(_fail("G-2", found.span, detail, citation_missing=in_policy))
        for amount in extract.amounts(sentence):
            paise = amount.value
            if paise in cited_amounts or (
                index and covered(run.cited_fields, index.amount_paths(paise))
            ):
                continue
            in_order = bool(index and index.amount_paths(paise))
            if policy_context or (paise in all_amounts and not in_order):
                in_policy = paise in all_amounts
                detail = (
                    "amount is in a policy passage the reply does not cite"
                    if in_policy
                    else ("amount is not in any cited policy passage")
                )
                failures.append(_fail("G-2", amount.span, detail, citation_missing=in_policy))
    return failures


# --- G-3 -----------------------------------------------------------------------------------


def check_promises(run: _Run) -> list[RuleFailure]:
    """G-3: language that promises an outcome or offers compensation always fails."""
    failures = []
    for pattern in run.config.compiled("commitment_patterns"):
        for match in pattern.finditer(run.text):
            failures.append(
                _fail("G-3", match.group(0), "promises an outcome or offers compensation")
            )
    return failures


# --- G-4 -----------------------------------------------------------------------------------


def check_citations_exist(run: _Run) -> list[RuleFailure]:
    """G-4: a cited policy ID must be in the loaded policy set, a cited field in the order."""
    failures = []
    for citation in run.citations:
        ref = citation.source_ref.strip()
        if citation.source_type is SourceType.POLICY:
            if ref not in run.context.policy_texts:
                failures.append(_fail("G-4", ref, "cites a policy ID that does not exist"))
        elif run.order_index is None:
            failures.append(_fail("G-4", ref, "cites an order field but there is no order record"))
        elif not run.order_index.has(ref):
            failures.append(
                _fail("G-4", ref, "cites an order field that is not in the order record")
            )
    return failures


# --- G-5 -----------------------------------------------------------------------------------

_CAPITALISED = re.compile(r"(?<=[a-z] )([A-Z][A-Za-z]{2,})\b")
_POLICY_REF = re.compile(r"\b[A-Z]{3}-\d+\.\d+\b")
_GREETING = re.compile(r"^(?:hi|hello|dear|namaste)\s+[^,.]{1,40},", re.IGNORECASE)


def check_uncited_claims(run: _Run) -> list[RuleFailure]:
    """G-5: claims the lists recognise (a cause, a handling step, a courier, a product property, a
    proper name) must come from the order, the customer's message or a cited policy."""
    failures: dict[str, RuleFailure] = {}
    config = run.config
    allowed = run.allowed_text
    words = _words(allowed)

    def flag(span: str, detail: str) -> None:
        failures.setdefault(span.lower(), _fail("G-5", span, detail))

    for pattern in config.compiled("causal_patterns"):
        for match in pattern.finditer(run.text):
            if match.group(0).lower() not in allowed:
                flag(match.group(0), "gives a reason that is not in the order or a cited policy")
    for term in config.process_terms:
        if re.search(rf"\b{re.escape(term)}\b", run.lower) and term.lower() not in allowed:
            flag(term, "describes handling that is not in the order record")
    for phrase in config.product_claim_phrases:
        if re.search(rf"\b{re.escape(phrase)}\b", run.lower) and phrase.lower() not in allowed:
            flag(phrase, "describes the product with no source")
    for courier in config.couriers:
        if re.search(rf"\b{re.escape(courier)}\b", run.lower) and courier.lower() not in allowed:
            flag(courier, "names a courier the order record does not contain")

    known = set(config.allowed_names)
    for position, sentence in enumerate(run.sentences):
        if position == 0:
            sentence = _GREETING.sub("", sentence)  # "Hello Priya," addresses the sender
        sentence = _POLICY_REF.sub("", sentence)  # "RET-1.1" is an ID, not a name
        for match in _CAPITALISED.finditer(sentence):
            name = match.group(1)
            if name not in known and name.lower() not in words:
                flag(
                    name, "names something that is not in the order, the message or a cited policy"
                )
    return list(failures.values())


# --- G-6 -----------------------------------------------------------------------------------


def check_privacy(run: _Run) -> list[RuleFailure]:
    """G-6: nothing about another customer's order: their address, their order number, or any
    field of an order that is not the sender's."""
    failures = []
    sender = run.context.customer_email.strip().lower()
    allowed = {sender, *(e.lower() for e in run.config.allowed_emails)}
    for address in extract.emails(run.text):
        if address not in allowed:
            failures.append(_fail("G-6", address, "an email address that is not the sender's"))

    order = run.context.order
    own_ids = {str(order["order_id"]).upper()} if order else set()
    for token in run.order_id_values:
        if token not in own_ids and token not in run.message_order_ids:
            failures.append(_fail("G-6", token, "an order number that is not this ticket's"))

    if order is not None and str(order.get("customer_email", "")).strip().lower() != sender:
        used = bool(run.cited_fields) or bool(run.order_id_values)
        if used:
            failures.append(
                _fail(
                    "G-6",
                    str(order.get("order_id", "")),
                    "uses an order that belongs to someone else",
                )
            )
    return failures


# --- G-7 -----------------------------------------------------------------------------------


def _shingles(text: str, size: int) -> set[tuple[str, ...]]:
    tokens = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(tokens[i : i + size]) for i in range(len(tokens) - size + 1)}


def check_injection_echo(run: _Run) -> list[RuleFailure]:
    """G-7: the reply must not obey an instruction found in the customer's message, or repeat it."""
    failures = []
    for pattern in run.config.compiled("obedience_patterns"):
        for match in pattern.finditer(run.text):
            failures.append(_fail("G-7", match.group(0), "appears to obey an instruction"))
    reply_shingles = _shingles(run.text, ECHO_WORDS)
    for sentence in extract.sentences(run.context.customer_message or ""):
        if scan_injection(None, sentence) and reply_shingles & _shingles(sentence, ECHO_WORDS):
            failures.append(
                _fail("G-7", sentence, "repeats an instruction from the customer's message")
            )
    return failures


# --- G-8 to G-10 ---------------------------------------------------------------------------


def check_sign_off(run: _Run) -> list[RuleFailure]:
    """G-8: the reply ends with the store's sign-off, exactly, on its own last line."""
    lines = run.reply.rstrip().splitlines()
    last = lines[-1].strip() if lines else ""
    if last == run.context.sign_off.strip():
        return []
    return [_fail("G-8", last, "does not end with the store sign-off")]


def check_apologies(run: _Run) -> list[RuleFailure]:
    """G-9: at most one apology."""
    found = [
        m.group(0) for p in run.config.compiled("apology_patterns") for m in p.finditer(run.text)
    ]
    if len(found) <= 1:
        return []
    return [_fail("G-9", ", ".join(found), f"{len(found)} apologies; at most one is allowed")]


def check_length(run: _Run) -> list[RuleFailure]:
    """G-10: the reply is within the configured length."""
    if len(run.reply) <= run.config.max_reply_chars:
        return []
    return [
        _fail(
            "G-10",
            run.reply[:40],
            f"{len(run.reply)} characters; the limit is {run.config.max_reply_chars}",
        )
    ]


RULE_CHECKS = (
    check_order_values,
    check_policy_values,
    check_promises,
    check_citations_exist,
    check_uncited_claims,
    check_privacy,
    check_injection_echo,
    check_sign_off,
    check_apologies,
    check_length,
)


def validate(
    reply_text: str,
    citations: Sequence[Citation],
    context: GroundingContext,
    config: GroundingConfig | None = None,
) -> ValidationResult:
    run = _Run(reply_text, citations, context, config or default_config())
    failures = [failure for check in RULE_CHECKS for failure in check(run)]
    return ValidationResult(failures=failures)
