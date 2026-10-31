from fastapi import APIRouter

from apps.api.deps import unauthorized
from apps.api.schemas import LoginRequest, RefreshRequest, TokenResponse
from mabeyn.auth import service
from mabeyn.auth.service import AuthenticationError

router = APIRouter(prefix="/auth", tags=["auth"])


def _response(pair: service.TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/login")
async def login(body: LoginRequest) -> TokenResponse:
    try:
        return _response(await service.login(body.email, body.password))
    except AuthenticationError:
        raise unauthorized() from None


@router.post("/refresh")
async def refresh(body: RefreshRequest) -> TokenResponse:
    try:
        return _response(await service.refresh(body.refresh_token))
    except AuthenticationError:
        raise unauthorized() from None
