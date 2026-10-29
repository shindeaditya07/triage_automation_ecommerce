"""Pre-lookup escalation (spec section 14.1): which tickets go to the owner with no draft before
any order is looked up.

A ticket escalates when ANY of these holds. Two independent sources back the first four, so a
missed model flag does not mean a missed escalation:

- abusive tone, a legal threat, suspected fraud, or a prompt injection, as the classifier flagged
  it OR the keyword backstop (ESC-8.2) or the structural injection scan found it;
- the message describes a hazard or an injury (`safety`, see `safety.py`): smoke, a burning smell,
  sparks, an electric shock, a fire. A replacement or a request for photos is the wrong first
  answer, so the owner reads it first;
- the message is mostly written in a non-Latin script (`non_english`), whatever the model made
  of it;
- classifier confidence below 0.70;
- intent `other` (no policy workflow applies).

This is plain deterministic code: no model call, the same input always gives the same answer.
Reasons use the eval vocabulary (`EscalationReason`) and are stored comma-joined on the ticket.
"""

from pydantic import Field

from services.api.escalation.injection import scan_injection
from services.api.escalation.keyword_backstop import scan
from services.api.escalation.language import is_non_english
from services.api.escalation.safety import scan_safety
from services.api.models.common import StrictModel
from services.api.models.enums import Flag, Intent
from services.api.models.ticket import Classification

CONFIDENCE_FLOOR = 0.70  # spec 14.1: strictly below this escalates

# Flag -> reason, in the order reasons are reported.
_FLAG_REASONS: tuple[tuple[Flag, str], ...] = (
    (Flag.ABUSIVE, "abusive"),
    (Flag.LEGAL_THREAT, "legal_threat"),
    (Flag.SUSPECTED_FRAUD, "suspected_fraud"),
    (Flag.PROMPT_INJECTION_SUSPECTED, "prompt_injection"),
)


class PreLookupDecision(StrictModel):
    escalate: bool
    reasons: list[str] = Field(default_factory=list)
    flags: list[Flag] = Field(
        default_factory=list,
        description="Classifier flags plus any the backstop or injection scan added.",
    )
    backstop_rules: list[str] = Field(default_factory=list)
    injection_signals: list[str] = Field(default_factory=list)
    safety_signals: list[str] = Field(default_factory=list)

    @property
    def reason(self) -> str:
        return ",".join(self.reasons)


def decide(classification: Classification, subject: str | None, body: str) -> PreLookupDecision:
    backstop = scan(subject, body)
    injection = scan_injection(subject, body)
    safety = scan_safety(subject, body)

    flags = list(classification.flags)
    for flag in [*backstop.flags, *([Flag.PROMPT_INJECTION_SUSPECTED] if injection else [])]:
        if flag not in flags:
            flags.append(flag)

    reasons = [reason for flag, reason in _FLAG_REASONS if flag in flags]
    if safety:
        reasons.append("safety")
    if is_non_english(subject, body):
        reasons.append("non_english")
    if classification.confidence < CONFIDENCE_FLOOR:
        reasons.append("low_confidence")
    if classification.intent is Intent.OTHER:
        reasons.append("intent_other")

    return PreLookupDecision(
        escalate=bool(reasons),
        reasons=reasons,
        flags=flags,
        backstop_rules=[match.rule_id for match in backstop.matches],
        injection_signals=injection,
        safety_signals=safety,
    )
