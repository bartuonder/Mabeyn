import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from mabeyn.agents.approvals import (
    ApprovalConflict,
    expire_stale_approvals,
    expired_approvals,
    resolve_approval,
)
from mabeyn.core.config import get_settings
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from tests.agents.harness import Brain, build_env
from tests.fakes import say, tool_call

REFUND = {"order_id": "KS-1001", "amount": 899.90, "reason": "Changed my mind"}


async def pending_refund(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"], tool_turns=[tool_call("request_refund", **REFUND), say("Done.")]
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    _, final = await env.ask("Refund KS-1001")
    assert final.status == "pending_approval"
    (approval,) = await env.approvals()
    return env, approval


async def age(engine: AsyncEngine, approval_id: uuid.UUID, hours: int) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE approvals SET expires_at = :t WHERE id = :id"),
            {"t": datetime.now(UTC) - timedelta(hours=hours), "id": approval_id},
        )


async def test_a_new_approval_gets_an_expiry_time(fresh_tenant, checkpointer):
    _, approval = await pending_refund(fresh_tenant, checkpointer)
    hours = (approval.expires_at - datetime.now(UTC)).total_seconds() / 3600
    assert get_settings().approval_ttl_hours - 0.1 < hours <= get_settings().approval_ttl_hours


async def test_an_approval_nobody_decides_expires_and_the_customer_is_told(
    fresh_tenant, checkpointer, admin_engine: AsyncEngine
):
    env, approval = await pending_refund(fresh_tenant, checkpointer)
    await age(admin_engine, approval.id, 1)
    assert approval.id in [a for _, a in await expired_approvals(10_000)]

    assert await expire_stale_approvals(env.service, limit=10_000) >= 1

    async with tenant_session(fresh_tenant) as session:
        stored = await session.get(m.Approval, approval.id)
        messages = list(
            await session.scalars(
                select(m.Message).where(m.Message.role == m.MessageRole.ASSISTANT)
            )
        )
    assert stored is not None and stored.status is m.ApprovalStatus.EXPIRED
    assert stored.decided_by is None and stored.decided_at is not None
    assert [c.status for c in await env.tool_calls()] == [m.ToolCallStatus.REJECTED]
    assert env.store.refunds == []
    assert messages and messages[-1].content == "Done."
    resumed = env.llm.tool_turns[-1][-1]["content"]
    assert "review period ended" in resumed and "human reviewer" not in resumed


async def test_approvals_that_are_still_in_time_are_left_alone(fresh_tenant, checkpointer):
    env, approval = await pending_refund(fresh_tenant, checkpointer)
    assert approval.id not in [a for _, a in await expired_approvals(10_000)]
    await expire_stale_approvals(env.service, limit=10_000)
    async with tenant_session(fresh_tenant) as session:
        stored = await session.get(m.Approval, approval.id)
    assert stored is not None and stored.status is m.ApprovalStatus.PENDING


async def test_an_expired_approval_cannot_be_decided_any_more(
    fresh_tenant, checkpointer, admin_engine: AsyncEngine
):
    env, approval = await pending_refund(fresh_tenant, checkpointer)
    await age(admin_engine, approval.id, 1)
    await expire_stale_approvals(env.service, limit=10_000)
    try:
        await resolve_approval(
            env.service, fresh_tenant, approval.id, approved=True, note=None, decided_by=None
        )
    except ApprovalConflict:
        pass
    else:
        raise AssertionError("an expired approval must not be decided")
    assert env.store.refunds == []
