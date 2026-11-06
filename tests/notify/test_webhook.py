import json

import httpx
import pytest
from sqlalchemy import select

from mabeyn.core.config import get_settings
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.notify.webhook import (
    SIGNATURE_HEADER,
    WebhookError,
    notify_pending_approvals,
    save_webhook,
    sign,
)
from tests.agents.test_approval_expiry import pending_refund

URL = "https://hooks.example.com/approvals"


async def public(host: str) -> list[str]:
    return ["93.184.216.34"]


async def private(host: str) -> list[str]:
    return ["10.0.0.5"]


class Receiver:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


async def mine(tenant_id, approval_id) -> m.Approval:
    async with tenant_session(tenant_id) as session:
        return await session.scalar(select(m.Approval).where(m.Approval.id == approval_id))


async def test_a_pending_approval_is_announced_once_with_a_valid_signature(
    fresh_tenant, checkpointer
):
    secret = await save_webhook(fresh_tenant, URL, public)
    env, approval = await pending_refund(fresh_tenant, checkpointer)
    receiver = Receiver()

    await notify_pending_approvals(transport=receiver.transport, resolver=public, limit=10_000)
    assert len(receiver.requests) == 1
    request = receiver.requests[0]
    body = json.loads(request.content)
    assert body["event"] == "approval.pending" and body["approval_id"] == str(approval.id)
    assert body["tool"] == "request_refund" and "customer_id" not in body
    assert request.headers[SIGNATURE_HEADER] == sign(secret, request.content)

    await notify_pending_approvals(transport=receiver.transport, resolver=public, limit=10_000)
    assert len(receiver.requests) == 1
    assert (await mine(fresh_tenant, approval.id)).notified_at is not None


async def test_a_failing_receiver_is_retried_a_limited_number_of_times(fresh_tenant, checkpointer):
    await save_webhook(fresh_tenant, URL, public)
    _, approval = await pending_refund(fresh_tenant, checkpointer)
    receiver = Receiver(status=500)

    for _ in range(get_settings().webhook_max_attempts + 2):
        await notify_pending_approvals(transport=receiver.transport, resolver=public, limit=10_000)
    ours = [r for r in receiver.requests if str(approval.id) in r.content.decode()]
    assert len(ours) == get_settings().webhook_max_attempts
    stored = await mine(fresh_tenant, approval.id)
    assert stored.notified_at is None and stored.status is m.ApprovalStatus.PENDING


async def test_setting_a_webhook_does_not_announce_older_requests(fresh_tenant, checkpointer):
    _, approval = await pending_refund(fresh_tenant, checkpointer)
    await save_webhook(fresh_tenant, URL, public)
    receiver = Receiver()
    await notify_pending_approvals(transport=receiver.transport, resolver=public, limit=10_000)
    assert not [r for r in receiver.requests if str(approval.id) in r.content.decode()]


async def test_a_tenant_without_a_webhook_is_never_called(fresh_tenant, checkpointer):
    _, approval = await pending_refund(fresh_tenant, checkpointer)
    receiver = Receiver()
    await notify_pending_approvals(transport=receiver.transport, resolver=public, limit=10_000)
    assert not [r for r in receiver.requests if str(approval.id) in r.content.decode()]


async def test_webhooks_cannot_point_at_private_addresses(fresh_tenant):
    with pytest.raises(WebhookError):
        await save_webhook(fresh_tenant, "http://10.0.0.5/hook", private)
    with pytest.raises(WebhookError):
        await save_webhook(fresh_tenant, "http://internal.example/hook", private)
