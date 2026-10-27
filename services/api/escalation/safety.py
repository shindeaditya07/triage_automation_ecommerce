"""Safety escalation: a message describing a hazard or an injury goes to the owner, with no draft.

A smoking motor, a burning smell, sparks, an electric shock or a burn is not a warranty claim, and
an automated reply that offers a replacement or asks for photos is the wrong first answer to a
customer whose child stood next to it. The owner decides what to say, and may need to tell the
customer to stop using the product or to report it.

Like the keyword backstop this is plain deterministic code that leans towards escalating: a false
match costs the owner one read, a miss means a cheerful draft about a fire. It matches only
what the customer says happened or is happening; "fireproof" or "smoke grey" do not match.
It runs on the plain and de-obfuscated views of the message (`textnorm`).
"""

import re

from services.api.textnorm import views

_HAZARD = re.compile(
    r"\b(?:"
    r"smok(?:e|es|ed|ing)"
    r"|smell(?:s|ed|ing)?\s+(?:of\s+|like\s+)?(?:burn(?:t|ing|ed)?|smoke|fire|plastic|gas)"
    r"|burn(?:t|ing)\s+smell|burnt|burning"
    r"|caught\s+fire|catch(?:es|ing)?\s+fire|on\s+fire|set\s+(?:\w+\s+)?on\s+fire|fire\s+hazard"
    r"|spark(?:s|ed|ing)?|short[\s-]?circuit(?:ed)?"
    r"|electric(?:al)?\s+shock(?:s|ed)?|(?:got|get|gave|give|giving|getting)\s+(?:me\s+)?a\s+shock"
    r"|shocked\s+(?:me|my|him|her|us)\b|electrocut\w*|current\s+(?:in|through)\s+the\s+body"
    r"|explo(?:de|ded|des|ding|sion)|burst\s+into\s+flames|flames?"
    r"|melt(?:ed|ing)\s+(?:down|plastic)?|gas\s+leak\w*|leak(?:s|ed|ing)?\s+gas"
    r"|injur(?:y|ed|ies)|got\s+hurt|was\s+hurt|were\s+hurt|burns?\s+on|burned\s+(?:my|his|her|the)"
    r"|hospital|ambulance"
    r")\b"
)
# "smoke grey", "smoked glass" and "smoke detector" describe a product, not an event.
_PRODUCT_WORDS = re.compile(
    r"\b(?:smoke|smoked)\s+(?:grey|gray|glass|colou?r|finish|detector|alarm)\b|\bfire[\s-]?proof\b"
)


def scan_safety(subject: str | None, body: str) -> list[str]:
    """The hazard words found in the message (empty when none), for the audit event."""
    found: list[str] = []
    for view in views(f"{subject or ''}\n{body}"):
        cleaned = _PRODUCT_WORDS.sub(" ", view)
        for match in _HAZARD.finditer(cleaned):
            word = match.group().strip()
            if word not in found:
                found.append(word)
    return found
