from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from mabeyn.auth.tokens import AdminClaims, TokenError, decode_access_token
from mabeyn.db.session import tenant_session

bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid or missing credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> AdminClaims:
    if credentials is None:
        raise unauthorized()
    try:
        return decode_access_token(credentials.credentials)
    except TokenError:
        raise unauthorized() from None


async def get_tenant_db(
    admin: Annotated[AdminClaims, Depends(get_admin)],
) -> AsyncIterator[AsyncSession]:
    async with tenant_session(admin.tenant_id) as session:
        yield session


Admin = Annotated[AdminClaims, Depends(get_admin)]
TenantDb = Annotated[AsyncSession, Depends(get_tenant_db)]
