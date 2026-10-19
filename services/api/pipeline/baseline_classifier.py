"""Keyword baseline classifier: the bar the model-based classifier has to beat.

Deterministic, text only. It scores each intent by the weighted keyword patterns that match and
takes the highest score. It sees no order data, so it cannot tell that an order does not exist, is
high value or falls outside policy, and it never notices a prompt injection. Those gaps are the
point: the eval report shows where the model earns its cost.

The patterns come from the intent definitions in the spec, not from tuning against the labeled
set. Do not tune them to the eval numbers; a baseline fitted to the test set stops being a
baseline.
"""

import re
from dataclasses import dataclass

from pydantic import Field

from services.api.escalation.keyword_backstop import BackstopResult
from services.api.models.common import StrictModel
from services.api.models.enums import Intent, Urgency
from services.api.textnorm import views


@dataclass(frozen=True)
class Signal:
    intent: Intent
    weight: float
    pattern: re.Pattern[str]


def _s(intent: Intent, weight: float, pattern: str) -> Signal:
    return Signal(intent, weight, re.compile(pattern))


SIGNALS: tuple[Signal, ...] = (
    # damaged_item
    _s(
        Intent.DAMAGED_ITEM,
        3,
        r"\b(?:broken|damaged|cracked|chipped|shattered|smashed|defective|torn|dented|scratched"
        r"|leaking|leaked|spoilt|spoiled|crushed)\b|\btoot(?:a|i|\s*kar)\b",
    ),
    _s(Intent.DAMAGED_ITEM, 1, r"\bdamage\b|\breplacement\b"),
    # cancellation
    _s(Intent.CANCELLATION, 4, r"\bcancel(?:l?ed|l?ing|lation)?\b"),
    # address_change
    _s(
        Intent.ADDRESS_CHANGE,
        4,
        r"\b(?:change|update|edit|correct|wrong|different|new)\s+(?:\w+\s+){0,2}address\b"
        r"|\baddress\b[^.!?\n]{0,25}\b(?:change|wrong|incorrect|mistake|update)",
    ),
    _s(Intent.ADDRESS_CHANGE, 3, r"\boffice address\b"),
    # payment
    _s(
        Intent.PAYMENT,
        2,
        r"\b(?:payments?|upi|cod|cash on delivery|debited|deducted|charged|transaction"
        r"|net\s?banking|invoice|gst)\b",
    ),
    _s(Intent.PAYMENT, 1, r"\bpaid\b|\bcard\b"),
    # refund
    _s(Intent.REFUND, 3, r"\brefunds?(?:ed)?\b|\bmoney back\b|\breimburse\w*\b"),
    # return
    _s(
        Intent.RETURN,
        3,
        r"\breturn(?:s|ed|ing)?\b|\bsend (?:it |this )?back\b|\bexchanges?\b",
    ),
    # warranty
    _s(Intent.WARRANTY, 4, r"\bwarrant(?:y|ies)\b|\bguarantee\b"),
    _s(Intent.WARRANTY, 2, r"\bstopped working\b"),
    # order_status
    _s(
        Intent.ORDER_STATUS,
        3,
        r"\bwhere\s+is\s+my\s+(?:order|parcel|package|shipment)\b|\border status\b",
    ),
    _s(
        Intent.ORDER_STATUS,
        2,
        r"\b(?:track(?:ing)?|dispatched|shipped|delivery date|late|delayed?|courier|awb"
        r"|not (?:yet )?(?:arrived|received|delivered)"
        r"|(?:has not|hasn't|have not|haven't) (?:arrived|reached|come)"
        r"|when will (?:it|my order))\b",
    ),
    _s(Intent.ORDER_STATUS, 0.5, r"\border\b"),
    # product_question
    _s(
        Intent.PRODUCT_QUESTION,
        2,
        r"\b(?:do you (?:sell|have|ship|offer|accept|do)|available|in stock|restock|capacity"
        r"|compatible|material|dimensions?|care instructions|gift wrap\w*"
        r"|shipping (?:cost|charges?)|free shipping|international|outside india|support hours"
        r"|what time)\b|\bhow (?:do|should|can) i (?:clean|use|wash|care)\b",
    ),
)

_SAFETY = re.compile(
    r"\b(?:fire|sparks?|sparked|smoke|smoking|burning smell|burnt|electric shock|shock"
    r"|injur(?:y|ed)|hurt|child(?:ren)?)\b"
)
_REPEAT = re.compile(
    r"\b(?:second|third|fourth|fifth|2nd|3rd|4th|5th)\s+(?:time|message|email|request)\b"
    r"|\bstill (?:not|no|waiting)\b|\bno reply\b|\bnobody (?:has )?(?:replied|responded)\b"
)
# Ties go to the earlier intent. Specific, actionable intents come before the generic ones.
PRIORITY: tuple[Intent, ...] = (
    Intent.DAMAGED_ITEM,
    Intent.CANCELLATION,
    Intent.ADDRESS_CHANGE,
    Intent.PAYMENT,
    Intent.REFUND,
    Intent.RETURN,
    Intent.WARRANTY,
    Intent.ORDER_STATUS,
    Intent.PRODUCT_QUESTION,
)
_URGENCY_BY_INTENT = {
    Intent.DAMAGED_ITEM: Urgency.HIGH,
    Intent.CANCELLATION: Urgency.HIGH,
    Intent.ADDRESS_CHANGE: Urgency.HIGH,
    Intent.PRODUCT_QUESTION: Urgency.LOW,
    Intent.OTHER: Urgency.LOW,
}


class BaselineClassification(StrictModel):
    intent: Intent
    urgency: Urgency
    scores: dict[str, float] = Field(description="Score per intent that matched at least once.")


def classify(
    subject: str | None, body: str, backstop: BackstopResult | None = None
) -> BaselineClassification:
    """Classify a message from its text. `backstop` raises urgency when a legal rule matched."""
    plain_view, loose_view = views(f"{subject or ''}\n{body}")
    scores: dict[Intent, float] = {}
    for signal in SIGNALS:
        if signal.pattern.search(plain_view) or signal.pattern.search(loose_view):
            scores[signal.intent] = scores.get(signal.intent, 0.0) + signal.weight
    # "order" alone is context, not evidence: it must not turn a vague message into order_status.
    if scores.get(Intent.ORDER_STATUS) == 0.5:
        del scores[Intent.ORDER_STATUS]
    intent = Intent.OTHER
    if scores:
        best = max(scores.values())
        intent = next(i for i in PRIORITY if scores.get(i) == best)
    return BaselineClassification(
        intent=intent,
        urgency=_urgency(intent, plain_view, backstop),
        scores={i.value: s for i, s in sorted(scores.items(), key=lambda kv: kv[0].value)},
    )


def _urgency(intent: Intent, text: str, backstop: BackstopResult | None) -> Urgency:
    if _SAFETY.search(text):
        return Urgency.CRITICAL
    if backstop is not None and any(m.rule_id.startswith("legal.") for m in backstop.matches):
        return Urgency.CRITICAL
    if _REPEAT.search(text):
        return Urgency.HIGH
    return _URGENCY_BY_INTENT.get(intent, Urgency.NORMAL)
