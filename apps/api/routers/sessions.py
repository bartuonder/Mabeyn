from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from apps.api.deps import unauthorized
from apps.api.schemas import SessionRequest, SessionResponse
from mabeyn.auth import service
from mabeyn.auth.service import AuthenticationError, SessionRefused
from mabeyn.core.config import get_settings
from mabeyn.core.ratelimit import RateLimiter, get_rate_limiter

router = APIRouter(prefix="/sessions", tags=["sessions"])

Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]


@router.post("")
async def create_session(
    body: SessionRequest,
    request: Request,
    limiter: Limiter,
    x_api_key: Annotated[str | None, Header()] = None,
    origin: Annotated[str | None, Header()] = None,
) -> SessionResponse:
    if not x_api_key:
        raise unauthorized()
    settings = get_settings()
    window = settings.session_rate_window_seconds
    address = request.client.host if request.client else "unknown"
    # The address limit comes first so a flood from one client cannot burn the key's allowance.
    if not await limiter.allow(f"session:ip:{address}", settings.session_rate_per_address, window):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sessions requested")
    try:
        session = await service.create_customer_session(x_api_key, body.customer_id, origin)
    except AuthenticationError:
        raise unauthorized() from None
    except SessionRefused as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    if not await limiter.allow(
        f"session:key:{session.key_id}", settings.session_rate_per_key, window
    ):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sessions requested")
    return SessionResponse(token=session.token, expires_in=session.expires_in)
