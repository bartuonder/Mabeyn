import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from apps.api.deps import Admin, TenantDb
from apps.api.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from mabeyn.auth.api_keys import generate_api_key
from mabeyn.db.models import ApiKey

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key(body: ApiKeyCreate, admin: Admin, db: TenantDb) -> ApiKeyCreated:
    generated = generate_api_key(body.kind.value)
    record = ApiKey(
        tenant_id=admin.tenant_id,
        name=body.name,
        prefix=generated.prefix,
        key_hash=generated.key_hash,
        kind=body.kind,
        allowed_origins=body.allowed_origins,
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    # The plain key is returned once and never stored.
    return ApiKeyCreated(**ApiKeyOut.model_validate(record).model_dump(), key=generated.plain)


@router.get("")
async def list_api_keys(db: TenantDb) -> list[ApiKeyOut]:
    rows = await db.scalars(select(ApiKey).order_by(ApiKey.created_at))
    return [ApiKeyOut.model_validate(row) for row in rows]


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(key_id: uuid.UUID, db: TenantDb) -> Response:
    record = await db.get(ApiKey, key_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "API key not found")
    if record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
