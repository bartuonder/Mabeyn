import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from apps.api.deps import Admin, TenantDb
from apps.api.schemas import ApprovalDecision, ApprovalOut, ApprovalResolved
from mabeyn.agents.approvals import ApprovalConflict, ApprovalMissing, resolve_approval
from mabeyn.agents.runtime import get_agent_service
from mabeyn.agents.service import AgentService
from mabeyn.db.models import Approval, ApprovalStatus

router = APIRouter(prefix="/approvals", tags=["approvals"])
log = structlog.get_logger()

Agents = Annotated[AgentService, Depends(get_agent_service)]


@router.get("")
async def list_approvals(
    db: TenantDb, status_filter: Annotated[ApprovalStatus | None, Query(alias="status")] = None
) -> list[ApprovalOut]:
    statement = select(Approval).order_by(Approval.created_at.desc())
    if status_filter is not None:
        statement = statement.where(Approval.status == status_filter)
    return [ApprovalOut.model_validate(row) for row in await db.scalars(statement)]


@router.get("/{approval_id}")
async def get_approval(approval_id: uuid.UUID, db: TenantDb) -> ApprovalOut:
    approval = await db.get(Approval, approval_id)
    if approval is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found")
    return ApprovalOut.model_validate(approval)


@router.post("/{approval_id}")
async def decide(
    approval_id: uuid.UUID, body: ApprovalDecision, admin: Admin, agents: Agents
) -> ApprovalResolved:
    """Record the decision and let the paused conversation continue."""
    try:
        approval, final = await resolve_approval(
            agents,
            admin.tenant_id,
            approval_id,
            approved=body.decision == "approve",
            note=body.note,
            decided_by=admin.admin_id,
        )
    except ApprovalMissing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Approval not found") from None
    except ApprovalConflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This request was already decided or has expired"
        ) from None
    except Exception:
        log.exception("approval_resume_failed", approval_id=str(approval_id))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "The conversation could not be resumed; try again"
        ) from None
    resolved = ApprovalOut.model_validate(approval)
    return ApprovalResolved(**resolved.model_dump(), answer=final.answer)
