import uuid
from datetime import timedelta

import jwt
import pytest

from mabeyn.auth import tokens
from mabeyn.core.config import get_settings

TENANT = uuid.uuid4()
ADMIN = uuid.uuid4()


def test_access_token_roundtrip():
    claims = tokens.decode_access_token(tokens.create_access_token(ADMIN, TENANT))
    assert claims == tokens.AdminClaims(admin_id=ADMIN, tenant_id=TENANT)


def test_refresh_token_roundtrip():
    jti, family = uuid.uuid4(), uuid.uuid4()
    claims = tokens.decode_refresh_token(tokens.create_refresh_token(jti, family, ADMIN, TENANT))
    assert claims == tokens.RefreshClaims(
        jti=jti, family_id=family, admin_id=ADMIN, tenant_id=TENANT
    )


def test_customer_token_roundtrip():
    claims = tokens.decode_customer_token(tokens.create_customer_token(TENANT, "cust-1"))
    assert claims == tokens.CustomerClaims(tenant_id=TENANT, customer_id="cust-1")


def test_token_types_are_not_interchangeable():
    access = tokens.create_access_token(ADMIN, TENANT)
    refresh = tokens.create_refresh_token(uuid.uuid4(), uuid.uuid4(), ADMIN, TENANT)
    customer = tokens.create_customer_token(TENANT, "cust-1")
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(refresh)
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(customer)
    with pytest.raises(tokens.TokenError):
        tokens.decode_refresh_token(access)
    with pytest.raises(tokens.TokenError):
        tokens.decode_customer_token(access)


def test_expired_token_is_rejected():
    expired = tokens.create_access_token(ADMIN, TENANT, ttl=timedelta(seconds=-1))
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(expired)


def test_tampered_signature_is_rejected():
    forged = jwt.encode(
        {"iss": "mabeyn", "iat": 0, "exp": 9999999999, "typ": "access", "sub": str(ADMIN)},
        "x" * 32,
        algorithm="HS256",
    )
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(forged)


def test_unsigned_token_is_rejected():
    unsigned = jwt.encode(
        {"iss": "mabeyn", "iat": 0, "exp": 9999999999, "typ": "access"}, None, algorithm="none"
    )
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(unsigned)


def test_missing_claims_are_rejected():
    secret = get_settings().jwt_secret.get_secret_value()
    incomplete = jwt.encode({"iss": "mabeyn", "typ": "access"}, secret, algorithm="HS256")
    with pytest.raises(tokens.TokenError):
        tokens.decode_access_token(incomplete)
