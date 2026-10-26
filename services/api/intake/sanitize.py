"""Turn untrusted message text into plain text.

Customer text is data, never markup and never instructions. This removes what could carry either
past a reader or a prompt: HTML tags and the content of elements hidden from view, script and
style blocks, links, comments, and invisible or control characters. It does not try to judge what
the remaining words mean; spotting an injection attempt is the escalation stage's job.

The text is first normalised to Unicode NFKC, so full-width letters and digits, ligatures and
similar look-alikes become their plain forms before anything is matched. That also means
a full-width "script" tag is an ordinary script tag by the time the HTML pass sees it.
"""

import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

LINK_PLACEHOLDER = "[link removed]"

_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
_ALWAYS_DROPPED = frozenset(
    {"script", "style", "template", "noscript", "head", "title", "iframe", "object"}
)
_LINE_BREAKS = frozenset(
    {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table"}
)
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em|rem|%)?\s*(?:;|!|$)"
    r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|!|$)"
    r"|text-indent\s*:\s*-\s*\d{3,}"
    r"|(?:left|top)\s*:\s*-\s*\d{3,}",
    re.IGNORECASE,
)
_LINK = re.compile(
    r"(?i)(?:\b(?:https?|ftp|mailto|javascript|data|file):[^\s<>\"']+|\bwww\.[^\s<>\"']+)"
)
_LEFTOVER_TAG = re.compile(r"</?[A-Za-z!][^<>]*(?:>|$)")
_SPACES = re.compile(r"[ \t\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class Cleaned:
    text: str
    html_found: bool = False
    hidden_text_removed: bool = False
    links_removed: int = 0


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_removed = False
        self._stack: list[tuple[str, bool]] = []
        self._hidden_depth = 0

    @staticmethod
    def _hidden(tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        if tag in _ALWAYS_DROPPED:
            return True
        for name, value in attrs:
            if name == "hidden" or (name == "aria-hidden" and (value or "").lower() == "true"):
                return True
            if name == "style" and value and _HIDDEN_STYLE.search(value):
                return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            if tag in _LINE_BREAKS and not self._hidden_depth:
                self.parts.append("\n")
            return
        hidden = self._hidden(tag, attrs)
        if tag in _LINE_BREAKS and not (hidden or self._hidden_depth):
            self.parts.append("\n")
        self._stack.append((tag, hidden))
        self._hidden_depth += hidden

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _LINE_BREAKS and not self._hidden_depth:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                was_hidden = self._stack[index][1]
                for _, hidden in self._stack[index:]:
                    self._hidden_depth -= hidden
                del self._stack[index:]
                if tag in _LINE_BREAKS and not (was_hidden or self._hidden_depth):
                    self.parts.append("\n")
                return
        if tag in _LINE_BREAKS and not self._hidden_depth:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            if data.strip() and not self._in_dropped_block():
                self.hidden_removed = True
            return
        self.parts.append(data)

    def _in_dropped_block(self) -> bool:
        """Script, style and the like are removed as a matter of course, not as hidden text."""
        return any(tag in _ALWAYS_DROPPED for tag, _ in self._stack)


def _strip_invisible(text: str) -> str:
    kept = []
    for char in text.replace("\r\n", "\n").replace("\r", "\n"):
        if char in "\n\t":
            kept.append(char)
        elif unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}:
            continue
        else:
            kept.append(char)
    return "".join(kept)


def clean_text(raw: str) -> Cleaned:
    """Plain text from untrusted input. Never raises on odd input; an empty result is possible."""
    text = unicodedata.normalize("NFKC", raw)
    html_found = False
    hidden_removed = False
    if "<" in text:
        extractor = _Extractor()
        extractor.feed(text)
        extractor.close()
        text = "".join(extractor.parts)
        text = _LEFTOVER_TAG.sub("", text)
        html_found = True
        hidden_removed = extractor.hidden_removed
    text = _strip_invisible(text)
    text, links = _LINK.subn(LINK_PLACEHOLDER, text)
    text = "\n".join(_SPACES.sub(" ", line).strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text).strip()
    return Cleaned(text, html_found, hidden_removed, links)
