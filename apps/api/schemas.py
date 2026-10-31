from pydantic import BaseModel, ConfigDict, EmailStr


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Response(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(StrictRequest):
    email: EmailStr
    password: str


class RefreshRequest(StrictRequest):
    refresh_token: str


class TokenResponse(Response):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
