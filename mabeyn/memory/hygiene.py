import re
import unicodedata
from dataclasses import dataclass

from mabeyn.memory.schema import MemoryRecord

_FOLD = str.maketrans("ışğüöç", "isguoc")

# Patterns run on text that is lower-cased and stripped of Turkish diacritics.
INSTRUCTION_PATTERNS = [
    r"\bfrom now on\b",
    r"\b(always|never) (approve|accept|refund|allow|grant|ignore|obey|follow|ask|verify|check|"
    r"require|refuse|reject|escalate|skip)\b",
    r"\bignore\b.{0,40}\b(instruction|rule|polic|previous|above|prior)",
    r"\b(system|developer) (prompt|message|instruction)",
    r"\b(you|assistant|agent|bot|chatbot|ai) (must|should|shall|will|need to|has to|have to)\b",
    r"\b(you are|act as|pretend|behave as|role ?play)\b",
    r"\b(remember|make sure|ensure|note) (to|that) (always|never|you|the assistant)\b",
    r"\bwithout (any )?(approval|review|verification|checking|asking|confirmation)\b",
    r"\b(auto|automatically)[- ]?(approve|refund|accept|grant)",
    r"\bpre[- ]?approved?\b",
    r"\b(override|bypass|disable|circumvent)\b",
    r"\b(is|are|was|were) (authorized|authorised|entitled|exempt|whitelisted|allowlisted)\b",
    r"\b(waive|waived)\b",
    r"\bno (approval|review|verification) (is )?(needed|required|necessary)\b",
    r"\b(vip|special|admin|staff|employee|manager) (status|access|rights|privileges|account)\b",
    r"\bbundan sonra\b",
    r"\b(her zaman|daima|mutlaka|asla|hicbir zaman)\b.{0,30}\b(onayla|kabul|iade|izin|sorma|"
    r"dogrula|reddetme|kontrol|ver)",
    r"\b(onceki|yukaridaki|tum) (talimat|kural|yonerge)",
    r"\b(talimat|yonerge|komut)\w*",
    r"\b(sistem|gelistirici) (istem|komut|mesaj|yonerge)",
    r"\bonay\w* (gerek|ihtiyac|sart)\w* (yok|duyma|degil)",
    r"\bonaysiz\b",
    r"\botomatik\w*\s+(onay|iade|kabul)",
    r"\bmusteri\w* (yetkili|ayricalikli)\b",
    r"\bonaylanmis\b",
    r"\b(yok say|gormezden gel|atla)\b",
    r"\b(yapay zeka|asistan|ajan|bot)\w*\s+(her zaman|mutlaka|asla|gerek|yapmal|yapmali|etmeli)",
]

PII_PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "iban": r"\b[a-z]{2}\d{2}(?:[ ]?\d{4}){3,}(?:[ ]?\d{1,4})?\b",
    "national_id": r"(?<!\d)\d{11}(?!\d)",
    "card_number": r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)",
    "phone": r"(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)",
    "secret": r"\b(password|passcode|parola|sifre|cvv|cvc|pin kodu|otp)\b",
    "url": r"(https?://|www\.)\S+",
}

_INSTRUCTION = [re.compile(p) for p in INSTRUCTION_PATTERNS]
_PII = {name: re.compile(p) for name, p in PII_PATTERNS.items()}
MIN_PHONE_DIGITS = 10
INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cn"})
_MARKUP = re.compile(r"[<>{}\[\]`]|\\n|\n")


@dataclass(frozen=True)
class Rejection:
    reason: str
    detail: str


def fold(text: str) -> str:
    """Lower-case, drop Turkish diacritics and compatibility forms so patterns stay simple."""
    text = unicodedata.normalize("NFKC", text).lower().replace("\u0307", "")
    text = "".join(c for c in text if unicodedata.category(c) not in INVISIBLE_CATEGORIES)
    return text.translate(_FOLD)


def screen(record: MemoryRecord) -> Rejection | None:
    """Return why a record must not be stored, or None when it is a plain fact."""
    return screen_text(record.text)


def screen_text(raw: str) -> Rejection | None:
    text = fold(raw)
    if _MARKUP.search(raw):
        return Rejection("markup", "markup or control characters")
    for name, pattern in _PII.items():
        for match in pattern.finditer(text):
            if name == "phone" and sum(c.isdigit() for c in match.group()) < MIN_PHONE_DIGITS:
                continue
            return Rejection("pii", name)
    for pattern in _INSTRUCTION:
        if pattern.search(text):
            return Rejection("instruction", pattern.pattern)
    return None
