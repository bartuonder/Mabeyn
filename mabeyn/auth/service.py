import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from mabeyn.auth import tokens
from mabeyn.auth.passwords import verify_dummy, verify_password
from mabeyn.core.config import get_settings
from mabeyn.db.models import AdminUser, RefreshToken
from mabeyn.db.session import anonymous_session, tenant_session


class AuthenticationError(Exception):
    pass


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


async def _issue_pair(
    session: AsyncSession, admin: AdminUser, family_id: uuid.UUID | None = None
) -> TokenPair:
    settings = get_settings()
    jti = uuid.uuid4()
    family_id = family_id or uuid.uuid4()
    session.add(
        RefreshToken(
            id=jti,
            tenant_id=admin.tenant_id,
            admin_user_id=admin.id,
            family_id=family_id,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    return TokenPair(
        access_token=tokens.create_access_token(admin.id, admin.tenant_id),
        refresh_token=tokens.create_refresh_token(jti, family_id, admin.id, admin.tenant_id),
        expires_in=settings.access_token_ttl_minutes * 60,
    )


async def login(email: str, password: str) -> TokenPair:
    async with anonymous_session() as session:
        statement = select(AdminUser).from_statement(
            text("SELECT * FROM auth_get_admin_by_email(:email)").bindparams(
                email=email.strip().lower()
            )
        )
        admin = (await session.scalars(statement)).one_or_none()

    if admin is None:
        verify_dummy(password)
        raise AuthenticationError("invalid credentials")
    if not verify_password(admin.password_hash, password):
        raise AuthenticationError("invalid credentials")

    async with tenant_session(admin.tenant_id) as session:
        return await _issue_pair(session, admin)


async def refresh(refresh_token: str) -> TokenPair:
    try:
        claims = tokens.decode_refresh_token(refresh_token)
    except tokens.TokenError as exc:
        raise AuthenticationError("invalid refresh token") from exc

    reuse_detected = False
    async with tenant_session(claims.tenant_id) as session:
        record = await session.get(RefreshToken, claims.jti, with_for_update=True)
        now = datetime.now(UTC)
        if record is None or record.revoked_at is not None or record.expires_at <= now:
            raise AuthenticationError("invalid refresh token")

        if record.used_at is not None:
            # A rotated token was presented again: revoke the whole family.
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == record.family_id)
                .values(revoked_at=now)
            )
            reuse_detected = True
        else:
            admin = await session.get(AdminUser, record.admin_user_id)
            if admin is None or not admin.is_active:
                raise AuthenticationError("invalid refresh token")
            record.used_at = now
            pair = await _issue_pair(session, admin, family_id=record.family_id)

    if reuse_detected:
        raise AuthenticationError("refresh token reuse detected")
    return pair
