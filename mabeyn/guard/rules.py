import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field

from mabeyn.core.text import fold

BLOCK = "block"
FLAG = "flag"
TAG_BLOCK = range(0xE0000, 0xE0080)
INVISIBLE_LIMIT = 3
MIN_DECODED_CHARS = 8
PRINTABLE_SHARE = 0.85

BASE64 = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{24,}={0,2}(?![A-Za-z0-9+/=])")
HEX = re.compile(r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{2}){16,}(?![0-9a-fA-F])")

# High-precision phrases, matched on folded text (lower case, no Turkish diacritics).
OVERRIDE_PATTERNS = [
    r"\b(ignore|disregard|forget|override|bypass)\b.{0,30}\b(previous|prior|above|earlier|all|any|"
    r"your|the)\b.{0,20}\b(instruction|rule|prompt|polic|guideline|restriction|programming)",
    r"\bforget (everything|all)\b.{0,30}\b(you|we|told|said|know)",
    r"\b(you are|you re|act as|pretend to be|behave as)\b.{0,25}\b(dan|jailbroken|unrestricted|"
    r"unfiltered|developer mode|evil|without (any )?(rules|restrictions|filters))",
    r"\b(developer|debug|god|jailbreak|dan) mode\b",
    r"\bdo anything now\b",
    r"\b(reveal|print|show|repeat|display|output|leak|tell me|write out)\b.{0,30}\b(your |the )?"
    r"(system|hidden|initial|secret|internal|original) (prompt|instruction|message|rule)",
    r"\b(repeat|print|output)\b.{0,20}\b(everything|all text|the text)\b.{0,15}\b(above|before)\b",
    r"\bwhat (is|are) your (system|hidden|initial) (prompt|instruction)",
    r"(^|\n)\s*(#{1,3}\s*)?(system|assistant)\s*[:\]]",
    r"<\|?(im_start|im_end|system|endoftext)\|?>",
    r"\[/?(inst|system)\]",
    r"\bonceki\b.{0,25}\b(talimat|kural|yonerge|komut)\w*\b.{0,25}\b(yok say|unut|dikkate alma|"
    r"gormezden gel|gecersiz)",
    r"\b(talimat|kural|yonerge)\w*\b.{0,20}\b(yok say|unut|gormezden gel)",
    r"\b(sistem|gelistirici)\s*(istem|prompt|komut|mesaj|talimat)\w*\b.{0,25}\b(goster|yaz|paylas|"
    r"tekrarla|ac|soyle)",
    r"\b(ignoriere|vergiss)\b.{0,30}\b(anweisung|regel|instruktion|alles)",
]
_OVERRIDE = [re.compile(p) for p in OVERRIDE_PATTERNS]


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    detail: str = ""


@dataclass
class Inspection:
    findings: list[Finding] = field(default_factory=list)
    decoded: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.severity == BLOCK for f in self.findings)

    @property
    def rule(self) -> str | None:
        return next((f.rule for f in self.findings if f.severity == BLOCK), None)

    @property
    def views(self) -> list[str]:
        return self.decoded


def _printable(text: str) -> bool:
    if len(text) < MIN_DECODED_CHARS:
        return False
    good = sum(c.isprintable() or c in "\n\t" for c in text)
    return good / len(text) >= PRINTABLE_SHARE


def decode_payloads(text: str) -> list[str]:
    """Readable text hidden in base64 or hex blobs."""
    found: list[str] = []
    for blob in BASE64.findall(text):
        try:
            decoded = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if _printable(decoded):
            found.append(decoded)
    for blob in HEX.findall(text):
        try:
            decoded = bytes.fromhex(blob).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
        if _printable(decoded):
            found.append(decoded)
    return found


def _invisible_count(raw: str) -> tuple[int, bool]:
    count, tags = 0, False
    for char in raw:
        if unicodedata.category(char) == "Cf" or ord(char) in TAG_BLOCK:
            count += 1
            tags = tags or ord(char) in TAG_BLOCK
    return count, tags


def override_match(text: str) -> str | None:
    folded = fold(text)
    return next((p.pattern for p in _OVERRIDE if p.search(folded)), None)


def inspect_input(raw: str) -> Inspection:
    """Cheap checks before any model: hidden characters, encoded payloads and known phrases."""
    result = Inspection()
    hidden, tags = _invisible_count(raw)
    if tags or hidden > INVISIBLE_LIMIT:
        result.findings.append(Finding("invisible_characters", BLOCK, f"{hidden} characters"))
    elif hidden:
        result.findings.append(Finding("invisible_characters", FLAG, f"{hidden} characters"))

    result.decoded = decode_payloads(raw)
    for decoded in result.decoded:
        result.findings.append(Finding("encoded_payload", FLAG, decoded[:80]))
        if override_match(decoded):
            result.findings.append(Finding("encoded_override", BLOCK, decoded[:80]))

    if pattern := override_match(raw):
        result.findings.append(Finding("override_phrase", BLOCK, pattern[:80]))
    return result
