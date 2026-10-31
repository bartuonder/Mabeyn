import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from apps.api.deps import TenantDb
from apps.api.schemas import ToolOut
from mabeyn.db.models import ToolDefinition

router = APIRouter(prefix="/tools", tags=["tools"])


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
