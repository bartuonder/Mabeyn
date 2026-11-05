import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, text

from mabeyn.agents.persist import save_assistant_message
from mabeyn.agents.service import AgentService, TurnContext
from mabeyn.db.models import Approval, ApprovalStatus, Conversation, Tenant
from mabeyn.db.session import anonymous_session, tenant_session
from mabeyn.rag.service import Final

log = structlog.get_logger()

EXPIRY_NOTE = "The review period ended without a decision."
BATCH = 50


class ApprovalMissing(Exception):
    pass


class ApprovalConflict(Exception):
    """The request was already decided or has expired."""


async def resolve_approval(
    service: AgentService,
    tenant_id: uuid.UUID,
    approval_id: uuid.UUID,
    *,
    approved: bool,
    note: str | None,
    decided_by: uuid.UUID | None,
    expired: bool = False,
) -> tuple[Approval, Final]:
    """Record a decision, let the paused conversation continue and store its final answer.

    If the conversation cannot be resumed the approval stays pending, so it can be retried.
    """
    async with tenant_session(tenant_id) as session:
        approval = await session.get(Approval, approval_id, with_for_update=True)
        if approval is None:
            raise ApprovalMissing
        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalConflict
        conversation = await session.get(Conversation, approval.conversation_id)
        if conversation is None:
            raise ApprovalMissing
        company = await session.scalar(select(Tenant.name)) or "the company"
        turn = TurnContext(tenant_id, conversation.customer_id, conversation.id, company)

        final = await service.resume(turn, approved, note, expired)

        approval.status = (
            ApprovalStatus.EXPIRED
            if expired
            else ApprovalStatus.APPROVED
            if approved
            else ApprovalStatus.REJECTED
        )
        approval.decided_by = decided_by
        approval.decided_at = datetime.now(UTC)
        await session.flush()
        await save_assistant_message(tenant_id, conversation.id, final)
        return approval, final


async def expired_approvals(limit: int = BATCH) -> list[tuple[uuid.UUID, uuid.UUID]]:
    async with anonymous_session() as session:
        rows = await session.execute(
            text("SELECT tenant_id, approval_id FROM expired_approvals(:n)"), {"n": limit}
        )
        return [(row.tenant_id, row.approval_id) for row in rows]


async def expire_stale_approvals(service: AgentService, limit: int = BATCH) -> int:
    """Close approvals nobody decided in time; the customer is told in the conversation."""
    closed = 0
    for tenant_id, approval_id in await expired_approvals(limit):
        try:
            await resolve_approval(
                service,
                tenant_id,
                approval_id,
                approved=False,
                note=EXPIRY_NOTE,
                decided_by=None,
                expired=True,
            )
            closed += 1
        except (ApprovalMissing, ApprovalConflict):
            continue
        except Exception:
            log.exception("approval_expiry_failed", approval_id=str(approval_id))
    return closed
