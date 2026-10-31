from typing import Annotated

from fastapi import APIRouter, Header

from apps.api.deps import unauthorized
from apps.api.schemas import SessionRequest, SessionResponse
from mabeyn.auth import service
from mabeyn.auth.service import AuthenticationError

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("")
async def create_session(
    body: SessionRequest,
    x_api_key: Annotated[str | None, Header()] = None,
) -> SessionResponse:
    if not x_api_key:
        raise unauthorized()
    try:
        token, expires_in = await service.create_customer_session(x_api_key, body.customer_id)
    except AuthenticationError:
        raise unauthorized() from None
    return SessionResponse(token=token, expires_in=expires_in)
