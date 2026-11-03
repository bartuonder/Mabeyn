import hashlib
import hmac
import re
import uuid

from mabeyn.core.config import get_settings

_NON_ALNUM = re.compile(r"[^a-z0-9]")
MIN_FRAGMENT = 10


def canary_for(tenant_id: uuid.UUID) -> str:
    """A stable secret marker per tenant that sits in prompts and must never reach a reply."""
    key = get_settings().jwt_secret.get_secret_value().encode()
    digest = hmac.new(key, f"canary:{tenant_id}".encode(), hashlib.sha256).hexdigest()
    return f"MBN-{digest[:16].upper()}"


def leaked(text: str, canary: str) -> bool:
    """True when the reply contains the canary, even broken up by spaces or punctuation."""
    squashed = _NON_ALNUM.sub("", text.lower())
    target = _NON_ALNUM.sub("", canary.lower())
    return target in squashed or target[:MIN_FRAGMENT] in squashed
