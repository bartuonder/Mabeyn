import unicodedata

INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc", "Co", "Cn"})
_FOLD = str.maketrans("ışğüöç", "isguoc")


def fold(text: str) -> str:
    """Lower-case, drop Turkish diacritics, compatibility forms and invisible characters."""
    text = unicodedata.normalize("NFKC", text).lower().replace("̇", "")
    text = "".join(c for c in text if unicodedata.category(c) not in INVISIBLE_CATEGORIES)
    return text.translate(_FOLD)
