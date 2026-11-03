import hashlib
import secrets
from dataclasses import dataclass

PREFIX_LENGTH = 12
PREFIXES = {"publishable": "mbn_pub_", "secret": "mbn_sec_"}


@dataclass(frozen=True)
class GeneratedApiKey:
    plain: str
    prefix: str
    key_hash: str


def hash_api_key(key: str) -> str:
    # Keys are high-entropy random strings, so a fast hash is sufficient.
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key(kind: str = "secret") -> GeneratedApiKey:
    plain = PREFIXES[kind] + secrets.token_urlsafe(32)
    return GeneratedApiKey(plain=plain, prefix=plain[:PREFIX_LENGTH], key_hash=hash_api_key(plain))
