import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
IBAN = re.compile(r"\b[A-Za-z]{2}\d{2}(?:[ ]?[A-Za-z0-9]{4}){2,7}(?:[ ]?[A-Za-z0-9]{1,4})?\b")
CARD = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?![\d-])")
NATIONAL_ID = re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")
PHONE = re.compile(
    r"(?<![\w.])(?:\+90|0090|90)?[ -]?\(?0?5\d{2}\)?[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}(?![\w])"
    r"|(?<![\w.])\+\d{1,3}[ -]?\(?\d{2,4}\)?(?:[ -]?\d{2,4}){2,4}(?![\w])"
)

LABELS = {
    "email": "[email]",
    "iban": "[iban]",
    "card": "[card number]",
    "national_id": "[national id]",
    "phone": "[phone]",
}


def luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def national_id_valid(digits: str) -> bool:
    """Turkish national identity number: 11 digits with two check digits."""
    if len(digits) != 11 or digits[0] == "0":
        return False
    d = [int(c) for c in digits]
    tenth = ((sum(d[0:9:2]) * 7) - sum(d[1:8:2])) % 10
    return d[9] == tenth and d[10] == sum(d[:10]) % 10


def iban_valid(value: str) -> bool:
    compact = re.sub(r"\s", "", value).upper()
    if not 15 <= len(compact) <= 34 or not compact[:2].isalpha() or not compact[2:4].isdigit():
        return False
    rearranged = compact[4:] + compact[:4]
    number = "".join(str(int(c, 36)) for c in rearranged)
    return int(number) % 97 == 1


@dataclass
class MaskResult:
    text: str
    kinds: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.kinds)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def mask_pii(text: str, keep: frozenset[str] = frozenset()) -> MaskResult:
    """Replace values that are validated personal data. Order numbers and totals stay intact.

    Values in `keep` (for example the shop's own contact details) are left as they are.
    """
    kinds: list[str] = []
    values: list[str] = []

    def replace(pattern: re.Pattern[str], kind: str, valid) -> None:  # type: ignore[no-untyped-def]
        nonlocal text

        def sub(match: re.Match[str]) -> str:
            if match.group() in keep or not valid(match.group()):
                return match.group()
            kinds.append(kind)
            values.append(match.group())
            return LABELS[kind]

        text = pattern.sub(sub, text)

    replace(IBAN, "iban", iban_valid)
    replace(EMAIL, "email", lambda _: True)
    replace(CARD, "card", lambda v: 13 <= len(_digits(v)) <= 19 and luhn_valid(_digits(v)))
    replace(NATIONAL_ID, "national_id", lambda v: national_id_valid(_digits(v)))
    replace(PHONE, "phone", lambda v: len(_digits(v)) >= 10)
    return MaskResult(text, kinds, values)
