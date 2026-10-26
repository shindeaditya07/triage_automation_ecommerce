"""Deterministic escalation backstop (ESC-8.2).

Regex rules for legal threats, chargebacks, consumer-court and regulator mentions, abusive or
threatening language and suspected fraud. They run beside the classifier's flags and either source
is enough to escalate, so escalation recall does not depend on a model's judgment alone.

The rules lean towards escalating: a false match costs the owner one read, a miss means an
automated reply to a customer who is threatening the store. They never decide an outcome for the
customer; a match only routes the ticket to the owner.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import Field

from services.api.models.common import StrictModel
from services.api.models.enums import Flag
from services.api.textnorm import views

_VOWELS = "aeiou"
_GAP = r"[.\-_]?"


def _loose(word: str) -> str:
    """Pattern for a word that may be masked (`f*ck`) or dotted out (`f.u.c.k`)."""
    parts = [f"[{c}*#@]" if c in _VOWELS else c for c in word]
    return _GAP.join(parts)


@dataclass(frozen=True)
class Rule:
    id: str
    flag: Flag
    description: str
    pattern: re.Pattern[str]

    def search(self, *texts: str) -> re.Match[str] | None:
        for text in texts:
            found = self.pattern.search(text)
            if found:
                return found
        return None


def _rule(rule_id: str, flag: Flag, description: str, pattern: str) -> Rule:
    return Rule(rule_id, flag, description, re.compile(pattern))


_SENTENCE = r"[^.!?\n]"
_LEGAL = Flag.LEGAL_THREAT
_ABUSIVE = Flag.ABUSIVE
_FRAUD = Flag.SUSPECTED_FRAUD

_PROFANITY = "|".join(
    [
        _loose("fuck") + r"(?:ing|ed|er|ers|s)?",
        _loose("shit") + r"(?:ty|s)?",
        _loose("bastard") + "s?",
        _loose("bitch") + "(?:es)?",
        _loose("asshole") + "s?",
        _loose("dickhead") + "s?",
        _loose("cunt") + "s?",
        r"chutiy[ae]s?",
        r"madarchod",
        r"be?hen\s?chod",
        r"bh?osd\w*",
        r"gandu",
        r"harami(?:s)?",
        r"kamin[ae]",
        r"kutt[ae]",
    ]
)
_INSULT_WORDS = (
    r"idiots?|morons?|imbeciles?|scum|crooks?|thieves|thief|cheaters?|looters?|fools?|liars?"
)
_HARM_VERBS = r"kill|hurt|beat|burn down|destroy|hunt|attack|stab|shoot|bomb|finish"
_HARM_TARGETS = (
    r"you|your|the (?:store|shop|warehouse|office|company|staff|place)|them|every(?:one|body)"
)

RULES: tuple[Rule, ...] = (
    # --- legal ------------------------------------------------------------------------------
    _rule(
        "legal.lawyer",
        _LEGAL,
        "A lawyer, attorney or solicitor is mentioned.",
        r"\b(?:lawyers?|attorneys?|solicitors?)\b|\bmy advocate\b",
    ),
    _rule(
        "legal.action",
        _LEGAL,
        "Legal action, a legal notice or a lawsuit is threatened.",
        r"\blegal (?:notice|action|team|proceedings?|case|recourse|steps)\b|\blawsuits?\b"
        r"|\b(?:sue|suing|sued)\s+(?:you|your|the (?:store|company|shop)|this)\b",
    ),
    _rule(
        "legal.court",
        _LEGAL,
        "A court, consumer forum or court case is threatened.",
        r"\b(?:consumer|district|high|supreme|civil)\s+(?:court|forum|commission)\b"
        rf"|\b(?:go|going|take|taking|taken|drag|dragging|move|moving)\b{_SENTENCE}{{0,30}}"
        r"\b(?:to|before)\s+(?:the\s+|a\s+)?court\b"
        r"|\b(?:file|filing|filed)\s+(?:a|an|the)\s+(?:legal\s+|civil\s+|criminal\s+)?case\b"
        r"|\bcourt (?:case|notice|summons|order)\b|\bsee you in court\b",
    ),
    _rule(
        "legal.chargeback",
        _LEGAL,
        "A chargeback or bank dispute is filed or threatened.",
        r"\bcharge[\s-]?backs?\b"
        rf"|\bdispute\b{_SENTENCE}{{0,30}}\b(?:bank|card|transaction|payment)\b"
        r"|\b(?:told|informed|called|contacted|asked)\s+(?:my|the)\s+"
        r"(?:bank|card company|credit card company)\b",
    ),
    _rule(
        "legal.regulator",
        _LEGAL,
        "A regulator or consumer body is named.",
        r"\bnational consumer helpline\b|\bconsumer (?:helpline|protection|affairs|commission"
        r"|rights|complaint)\b|\b(?:ccpa|rbi|ombudsman|regulator)\b",
    ),
    _rule(
        "legal.police",
        _LEGAL,
        "The police, cyber crime or an FIR is mentioned.",
        r"\bpolice\b|\bcyber\s?crime\b"
        r"|\b(?:file|filed|filing|lodge|lodged|register|registered)\s+(?:a\s+|an\s+|the\s+)?"
        r"(?:police\s+)?(?:fir|f\.i\.r\.?)\b",
    ),
    _rule(
        "legal.hindi",
        _LEGAL,
        "A lawyer, court, consumer or police term in Hindi.",
        r"वकील|कोर्ट|अदालत|उपभोक्ता|पुलिस|थाने",
    ),
    # --- abusive ----------------------------------------------------------------------------
    _rule(
        "abusive.profanity",
        _ABUSIVE,
        "Profanity, including masked and Hinglish forms.",
        rf"\b(?:{_PROFANITY})\b",
    ),
    _rule(
        "abusive.insult",
        _ABUSIVE,
        "An insult aimed at the store or its staff.",
        r"\b(?:idiots?|morons?|imbeciles?|scumbags?)\b"
        rf"|\b(?:you|your|you're|youre|ur)\b{_SENTENCE}{{0,40}}?\b(?:{_INSULT_WORDS}|clowns?)\b",
    ),
    _rule(
        "abusive.threat",
        _ABUSIVE,
        "A threat of violence or destruction.",
        rf"\b(?:will|shall|gonna|going to|'ll|ll)\s+(?:\w+\s+){{0,3}}?(?:{_HARM_VERBS})\s+"
        rf"(?:{_HARM_TARGETS})\b",
    ),
    _rule(
        "abusive.regret",
        _ABUSIVE,
        "Wishing harm on staff or promising they will regret it.",
        rf"\b(?:you|your \w+|they)(?:'ll| will)\s+regret\b|\bmake sure\b{_SENTENCE}{{0,30}}"
        rf"\b(?:regret|suffer|pay for)\b|\bhope\b{_SENTENCE}{{0,40}}\b(?:suffer|die|rot)\b",
    ),
    _rule(
        "abusive.visit",
        _ABUSIVE,
        "A threat to turn up at the store's premises.",
        r"\b(?:come|coming|show up|visit|reach|reaching)\s+(?:to|at|in)?\s*(?:your|the)\s+"
        rf"(?:warehouse|office|shop|store|house|home|godown|place)\b{_SENTENCE}{{0,80}}"
        r"\b(?:regret|pay for|teach|lesson|sorry|handle|deal with)\b",
    ),
    # --- fraud ------------------------------------------------------------------------------
    _rule(
        "fraud.third_party_data",
        _FRAUD,
        "Asks for another person's order, phone, address or details.",
        r"\b(?:send|give|share|tell|forward|email)\s+me\s+(?:his|her|their|someone(?:'s| else's)"
        r"|another (?:person|customer)'s|the other (?:person|customer)'s)\s+(?:\w+\s+){0,2}"
        r"(?:order|phone|address|details?|number|email|data|info)\b",
    ),
    _rule(
        "fraud.other_order",
        _FRAUD,
        "The order is said to belong to someone else.",
        rf"\b(?:order|account|parcel|package)\b{_SENTENCE}{{0,25}}\bbelongs?\s+to\b"
        rf"{_SENTENCE}{{0,20}}\b(?:friend|neighbou?r|colleague|boss|ex|someone else|another "
        r"(?:person|customer)|stranger|relative|cousin|brother|sister|husband|wife|uncle|aunt)\b",
    ),
    _rule(
        "fraud.keyword",
        _FRAUD,
        "Fraud, scam or unauthorised-use wording.",
        r"\b(?:fraud(?:ulent|ster|s)?|scams?|scammers?|swindl(?:e|ed|ing|er)"
        r"|fake\s+(?:order|account|identity|id)|stolen\s+(?:card|identity|credit card)"
        r"|unauthori[sz]ed\s+(?:transaction|payment|charge|purchase|use))\b",
    ),
)

RULES_BY_ID = {rule.id: rule for rule in RULES}


class BackstopMatch(StrictModel):
    rule_id: str
    flag: Flag
    excerpt: str = Field(description="The matched text from the normalised message.")


class BackstopResult(StrictModel):
    matches: list[BackstopMatch] = Field(default_factory=list)

    @property
    def escalate(self) -> bool:
        return bool(self.matches)

    @property
    def flags(self) -> list[Flag]:
        """Distinct flags, in the order the rules are declared."""
        seen: dict[Flag, None] = {}
        for match in self.matches:
            seen.setdefault(match.flag)
        return list(seen)

    @property
    def reasons(self) -> list[str]:
        """Escalation reasons (the flag names, which are also the labelled reasons)."""
        return [flag.value for flag in self.flags]


def scan(subject: str | None, body: str, rules: Iterable[Rule] = RULES) -> BackstopResult:
    """Run every rule over the subject and body and report each rule that matches."""
    text = f"{subject or ''}\n{body}"
    plain_view, loose_view = views(text)
    matches = []
    for rule in rules:
        found = rule.search(plain_view, loose_view)
        if found:
            matches.append(BackstopMatch(rule_id=rule.id, flag=rule.flag, excerpt=found.group()))
    return BackstopResult(matches=matches)
