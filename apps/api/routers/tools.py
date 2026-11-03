import uuid

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.api.deps import Admin, TenantDb
from apps.api.schemas import ToolCreate, ToolOut
from mabeyn.db.models import ToolDefinition
from mabeyn.tools.schema import ToolSchemaError
from mabeyn.tools.service import validate_definition

router = APIRouter(prefix="/tools", tags=["tools"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_tool(body: ToolCreate, admin: Admin, db: TenantDb) -> ToolOut:
    try:
        validate_definition(body.name, body.json_schema, body.http_method, body.url_template)
    except ToolSchemaError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None

    tool = ToolDefinition(
        tenant_id=admin.tenant_id,
        name=body.name,
        description=body.description,
        json_schema=body.json_schema,
        risk_level=body.risk_level,
        http_method=body.http_method,
        url_template=body.url_template,
        requires_identity=body.requires_identity,
    )
    db.add(tool)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A tool with this name exists") from None
    await db.refresh(tool)
    return ToolOut.model_validate(tool)


@router.get("")
async def list_tools(db: TenantDb) -> list[ToolOut]:
    rows = await db.scalars(select(ToolDefinition).order_by(ToolDefinition.name))
    return [ToolOut.model_validate(row) for row in rows]


@router.get("/{tool_id}")
async def get_tool(tool_id: uuid.UUID, db: TenantDb) -> ToolOut:
    tool = await db.get(ToolDefinition, tool_id)
    if tool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tool not found")
    return ToolOut.model_validate(tool)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(tool_id: uuid.UUID, db: TenantDb) -> Response:
    tool = await db.get(ToolDefinition, tool_id)
    if tool is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tool not found")
    await db.delete(tool)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
