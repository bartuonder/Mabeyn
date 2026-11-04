import enum
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from mabeyn.core.config import get_settings

ALGORITHM = "HS256"
ISSUER = "mabeyn"


class TokenError(Exception):
    pass


class TokenType(enum.StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"
    CUSTOMER = "customer"


@dataclass(frozen=True)
class AdminClaims:
    admin_id: uuid.UUID
    tenant_id: uuid.UUID


@dataclass(frozen=True)
class RefreshClaims:
    jti: uuid.UUID
    family_id: uuid.UUID
    admin_id: uuid.UUID
    tenant_id: uuid.UUID


@dataclass(frozen=True)
class CustomerClaims:
    tenant_id: uuid.UUID
    customer_id: str
    anonymous: bool = False
    key_id: uuid.UUID | None = None


def _encode(token_type: TokenType, ttl: timedelta, claims: dict[str, Any]) -> str:
    now = datetime.now(UTC)
    payload = {"iss": ISSUER, "iat": now, "exp": now + ttl, "typ": token_type.value, **claims}
    return jwt.encode(payload, get_settings().jwt_secret.get_secret_value(), algorithm=ALGORITHM)


def _decode(token: str, expected: TokenType) -> dict[str, Any]:
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            get_settings().jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "iss", "typ"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("invalid token") from exc
    if payload["typ"] != expected.value:
        raise TokenError("wrong token type")
    return payload


def _uuid(payload: dict[str, Any], key: str) -> uuid.UUID:
    try:
        return uuid.UUID(payload[key])
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError(f"invalid claim: {key}") from exc


def create_access_token(
    admin_id: uuid.UUID, tenant_id: uuid.UUID, ttl: timedelta | None = None
) -> str:
    ttl = ttl or timedelta(minutes=get_settings().access_token_ttl_minutes)
    return _encode(TokenType.ACCESS, ttl, {"sub": str(admin_id), "tid": str(tenant_id)})


def decode_access_token(token: str) -> AdminClaims:
    payload = _decode(token, TokenType.ACCESS)
    return AdminClaims(admin_id=_uuid(payload, "sub"), tenant_id=_uuid(payload, "tid"))


def create_refresh_token(
    jti: uuid.UUID,
    family_id: uuid.UUID,
    admin_id: uuid.UUID,
    tenant_id: uuid.UUID,
    ttl: timedelta | None = None,
) -> str:
    ttl = ttl or timedelta(days=get_settings().refresh_token_ttl_days)
    claims = {
        "jti": str(jti),
        "fid": str(family_id),
        "sub": str(admin_id),
        "tid": str(tenant_id),
    }
    return _encode(TokenType.REFRESH, ttl, claims)


def decode_refresh_token(token: str) -> RefreshClaims:
    payload = _decode(token, TokenType.REFRESH)
    return RefreshClaims(
        jti=_uuid(payload, "jti"),
        family_id=_uuid(payload, "fid"),
        admin_id=_uuid(payload, "sub"),
        tenant_id=_uuid(payload, "tid"),
    )


def create_customer_token(
    tenant_id: uuid.UUID,
    customer_id: str,
    ttl: timedelta | None = None,
    anonymous: bool = False,
    key_id: uuid.UUID | None = None,
) -> str:
    ttl = ttl or timedelta(minutes=get_settings().customer_token_ttl_minutes)
    claims: dict[str, Any] = {"tid": str(tenant_id), "cid": customer_id, "anon": anonymous}
    if key_id is not None:
        claims["kid"] = str(key_id)
    return _encode(TokenType.CUSTOMER, ttl, claims)


def decode_customer_token(token: str) -> CustomerClaims:
    payload = _decode(token, TokenType.CUSTOMER)
    customer_id = payload.get("cid")
    if not isinstance(customer_id, str) or not customer_id:
        raise TokenError("invalid claim: cid")
    return CustomerClaims(
        tenant_id=_uuid(payload, "tid"),
        customer_id=customer_id,
        anonymous=payload.get("anon") is True,
        key_id=_uuid(payload, "kid") if "kid" in payload else None,
    )
