"""Structural prompt-injection detection (spec section 16).

Looks for text that is shaped like an instruction to the system rather than a message to the
store: our own delimiter tags, chat-template tokens, a "system:" turn, "ignore your instructions"
and "you are now in admin mode" phrasing. It runs beside the classifier's
`prompt_injection_suspected` flag and either is enough to escalate.

The signals are phrases an ordinary customer has no reason to write, not topics. A customer who
asks for a refund is not an injection; one who tells the assistant to ignore its rules is. Like
the keyword backstop, a match only routes the ticket to the owner, who sees the original message.
"""

import re
from dataclasses import dataclass

from services.api.textnorm import views

_SENTENCE = r"[^.!?]"
_TAGS = (
    "customer_message|customer_subject|order_record|policy_passage|rejected_because|sign_off"
    "|system|assistant|developer|instructions?|prompt|reply|citations"
)


@dataclass(frozen=True)
class Signal:
    id: str
    description: str
    pattern: re.Pattern[str]


def _signal(signal_id: str, description: str, pattern: str) -> Signal:
    return Signal(signal_id, description, re.compile(pattern))


SIGNALS: tuple[Signal, ...] = (
    _signal(
        "injection.delimiter",
        "One of our own delimiter or role tags.",
        rf"</?\s*(?:{_TAGS})\b[^>]{{0,40}}>",
    ),
    _signal(
        "injection.template_token",
        "A chat-template token.",
        r"<\|[a-z_]{2,20}\|>|\[/?(?:inst|sys)\]|<<\s*/?sys\s*>>|###\s*(?:system|instruction)",
    ),
    _signal(
        "injection.role_turn",
        "A line that starts a system, assistant or developer turn.",
        r"(?:^|[.!?:>] )(?:system|assistant|developer|admin) ?(?:message|prompt)? ?:",
    ),
    _signal(
        "injection.override",
        "Orders to ignore or override the rules or instructions.",
        rf"\b(?:ignore|disregard|forget|override|bypass|skip)\b{_SENTENCE}{{0,30}}"
        rf"\b(?:previous|prior|above|earlier|all|any|your|the|these|store|safety)\b{_SENTENCE}{{0,25}}"
        r"\b(?:instructions?|rules?|prompts?|polic(?:y|ies)|guidelines?|restrictions?|guardrails?)\b",
    ),
    _signal(
        "injection.new_instructions",
        "Announces new or updated instructions.",
        r"\b(?:new|updated|revised|additional)\s+(?:system\s+)?instructions?\b"
        r"|\binstructions? (?:from|by) the (?:store )?(?:owner|admin|developer|system)\b",
    ),
    _signal(
        "injection.persona",
        "Tells the assistant it is now something else or in a special mode.",
        rf"\byou are (?:now|no longer)\b{_SENTENCE}{{0,40}}"
        r"\b(?:owner|admin|administrator|developer|assistant|bot|ai|mode|unrestricted|jailbroken)\b"
        r"|\b(?:admin|developer|debug|god|dan|jailbreak(?:en)?|unrestricted) mode\b|\bjailbreak\b",
    ),
    _signal(
        "injection.reveal",
        "Asks for the prompt, instructions, keys or credentials.",
        rf"\b(?:reveal|show|print|repeat|output|leak|tell me)\b{_SENTENCE}{{0,25}}"
        r"\b(?:system prompt|your (?:prompt|instructions)|hidden instructions"
        r"|(?:api|secret) keys?|passwords?|credentials?|login)\b",
    ),
)


def scan_injection(subject: str | None, body: str) -> list[str]:
    """Ids of the signals found in the subject and body, in the order declared."""
    plain_view, loose_view = views(f"{subject or ''}\n{body}")
    return [
        signal.id
        for signal in SIGNALS
        if signal.pattern.search(plain_view) or signal.pattern.search(loose_view)
    ]
