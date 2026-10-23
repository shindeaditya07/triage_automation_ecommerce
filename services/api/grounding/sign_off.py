"""The store's sign-off is added by code, not left to the model (rule G-8).

In the first live eval 29 draft attempts failed G-8 and 11 of 58 tickets ended on the fallback
template. The failed text was not saved, so the exact cause is unproven; the likely one is a closing
line of the model's own ("Best regards," or another team name). The sign-off is fixed text, not a
claim, so it is not the model's to get right. G-8 still checks every draft and every owner edit,
so this only repairs what the model hands over.

The model's own closing is removed first so the reply does not end with two. A trailing line counts
as a closing only if it is blank, is the store name with a team word after it, or starts with a
closing word ("regards", "thanks", "sincerely", ...) and either ends in a comma or is at most three
words. A real last sentence such as "Thank you for contacting us." stays.
"""

import re

_CLOSING_WORDS = {
    "regards",
    "thanks",
    "thank",
    "sincerely",
    "best",
    "warm",
    "kind",
    "cheers",
    "yours",
    "cordially",
    "respectfully",
}
_SHORT_CLOSING_WORDS = 3
_NAME_WORDS = 1  # the first word of the sign-off names the store in any variation of it
_TEAM_WORDS = {
    "home",
    "goods",
    "support",
    "team",
    "store",
    "customer",
    "care",
    "service",
    "help",
    "desk",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _is_closing(line: str, sign_off: str) -> bool:
    words = _words(line)
    if not words:
        return True
    name = _words(sign_off)[:_NAME_WORDS]
    named = words[1:] if words[0] == "the" else words
    if name and named[: len(name)] == name and set(named[len(name) :]) <= _TEAM_WORDS:
        return True
    if words[0] not in _CLOSING_WORDS:
        return False
    return line.rstrip().endswith(",") or len(words) <= _SHORT_CLOSING_WORDS


def with_sign_off(reply_text: str, sign_off: str) -> str:
    """The reply with any closing of the model's own removed and the exact sign-off last."""
    lines = reply_text.rstrip().splitlines()
    while lines and _is_closing(lines[-1], sign_off):
        lines.pop()
    body = "\n".join(lines).rstrip()
    return f"{body}\n\n{sign_off}" if body else sign_off
