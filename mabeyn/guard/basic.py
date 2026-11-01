import re
import unicodedata
from dataclasses import dataclass

MAX_INPUT_CHARS = 2000
# Zero-width and bidirectional control characters are stripped; ordinary text is untouched.
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿]")


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    text: str
    reason: str | None = None


def check_input(text: str) -> GuardDecision:
    """First-line hygiene. Injection detection and policies arrive with the firewall phase."""
    cleaned = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    cleaned = "".join(c for c in cleaned if c in "\n\t" or unicodedata.category(c)[0] != "C")
    cleaned = cleaned.strip()
    if not cleaned:
        return GuardDecision(False, "", "empty message")
    if len(cleaned) > MAX_INPUT_CHARS:
        return GuardDecision(False, cleaned[:MAX_INPUT_CHARS], "message is too long")
    return GuardDecision(True, cleaned)


def check_output(text: str) -> GuardDecision:
    """Placeholder for PII masking and canary checks."""
    return GuardDecision(True, text.strip())
