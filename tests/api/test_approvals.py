import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import app
from apps.kargoshop_mock.main import Store, create_app
from mabeyn.agents.runtime import get_agent_service
from mabeyn.agents.service import SupervisorService
from mabeyn.auth.tokens import create_customer_token
from mabeyn.core.config import get_settings
from mabeyn.db import models as m
from mabeyn.seed import register_kargoshop_tools
from mabeyn.tools.executor import HttpToolExecutor
from mabeyn.tools.service import ToolRunner
from tests.agents.harness import SHOP_URL, Brain, StubRag
from tests.api.test_api_keys import auth_headers
from tests.fakes import FakeLLM, say, tool_call

REFUND = {"order_id": "KS-1001", "amount": 899.90, "reason": "Changed my mind"}


@pytest.fixture
def shop(monkeypatch):
    monkeypatch.setenv("TOOL_ALLOWED_HOSTS", "shop")
    get_settings.cache_clear()
    yield Store()
    monkeypatch.delenv("TOOL_ALLOWED_HOSTS")
    get_settings.cache_clear()


@pytest.fixture
async def wired(shop, checkpointer, tenant_x):
    """The real supervisor behind the API, with a scripted model and the mock shop."""
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("request_refund", **REFUND), say("Your refund was submitted.")],
    )
    llm = FakeLLM(brain.chat, brain.tools)
    runner = ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(shop))))
    service = SupervisorService(llm, StubRag(), runner, checkpointer)
    app.dependency_overrides[get_agent_service] = lambda: service
    await register_kargoshop_tools(tenant_x.id, SHOP_URL)
    yield shop, brain
    app.dependency_overrides.clear()


def customer_headers(tenant_id: uuid.UUID, customer: str = "customer-1") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_customer_token(tenant_id, customer)}"}


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


async def request_refund(client: AsyncClient, tenant_id: uuid.UUID) -> tuple[str, str]:
    response = await client.post(
        "/chat", json={"message": "Refund KS-1001"}, headers=customer_headers(tenant_id)
    )
    parsed = events(response.text)
    kinds = [k for k, _ in parsed]
    assert "approval" in kinds and kinds[-1] == "done"
    conversation = next(d for k, d in parsed if k == "meta")["conversation_id"]
    approval = next(d for k, d in parsed if k == "approval")["approval_id"]
    return conversation, approval


async def test_chat_pauses_and_the_customer_is_told_it_is_under_review(
    client: AsyncClient, wired, tenant_x
):
    shop, _ = wired
    response = await client.post(
        "/chat", json={"message": "Refund KS-1001"}, headers=customer_headers(tenant_x.id)
    )
    parsed = events(response.text)
    done = next(d for k, d in parsed if k == "done")
    assert done["answer"] == "Your request is under review."
    assert shop.refunds == []


async def test_admin_lists_and_reads_pending_approvals(client: AsyncClient, wired, tenant_x):
    _, approval_id = await request_refund(client, tenant_x.id)
    headers = await auth_headers(client, tenant_x)

    pending = (await client.get("/approvals", params={"status": "pending"}, headers=headers)).json()
    mine = next(a for a in pending if a["id"] == approval_id)
    assert mine["payload"]["tool"] == "request_refund"
    assert mine["payload"]["arguments"]["amount"] == 899.9
    one = (await client.get(f"/approvals/{approval_id}", headers=headers)).json()
    assert one["status"] == "pending" and one["decided_by"] is None


async def test_approving_runs_the_tool_and_delivers_the_answer(
    client: AsyncClient, wired, tenant_x
):
    shop, _ = wired
    conversation, approval_id = await request_refund(client, tenant_x.id)
    headers = await auth_headers(client, tenant_x)

    response = await client.post(
        f"/approvals/{approval_id}", json={"decision": "approve"}, headers=headers
    )
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "approved" and body["answer"] == "Your refund was submitted."
    assert body["decided_by"] is not None and body["decided_at"] is not None
    assert [r["order_id"] for r in shop.refunds] == ["KS-1001"]

    # The customer picks the result up from the conversation.
    detail = await client.get(f"/chat/{conversation}", headers=customer_headers(tenant_x.id))
    texts = [msg["content"] for msg in detail.json()["messages"]]
    assert texts[-1] == "Your refund was submitted." and "Your request is under review." in texts


async def test_rejecting_never_runs_the_tool(client: AsyncClient, wired, tenant_x):
    shop, brain = wired
    brain.tool_turns[1:] = [say("A colleague declined the refund.")]
    _, approval_id = await request_refund(client, tenant_x.id)
    headers = await auth_headers(client, tenant_x)

    response = await client.post(
        f"/approvals/{approval_id}",
        json={"decision": "reject", "note": "Outside policy"},
        headers=headers,
    )
    assert response.json()["status"] == "rejected" and shop.refunds == []
    assert response.json()["answer"] == "A colleague declined the refund."


async def test_a_decision_can_only_be_made_once(client: AsyncClient, wired, tenant_x):
    _, approval_id = await request_refund(client, tenant_x.id)
    headers = await auth_headers(client, tenant_x)
    first = await client.post(
        f"/approvals/{approval_id}", json={"decision": "approve"}, headers=headers
    )
    second = await client.post(
        f"/approvals/{approval_id}", json={"decision": "reject"}, headers=headers
    )
    assert first.status_code == 200 and second.status_code == 409


async def test_other_tenants_cannot_see_or_decide(client: AsyncClient, wired, tenant_x, tenant_y):
    _, approval_id = await request_refund(client, tenant_x.id)
    other = await auth_headers(client, tenant_y)

    visible = {a["id"] for a in (await client.get("/approvals", headers=other)).json()}
    assert approval_id not in visible
    assert (await client.get(f"/approvals/{approval_id}", headers=other)).status_code == 404
    denied = await client.post(
        f"/approvals/{approval_id}", json={"decision": "approve"}, headers=other
    )
    assert denied.status_code == 404


async def test_approvals_require_an_admin_token(client: AsyncClient, wired, tenant_x):
    _, approval_id = await request_refund(client, tenant_x.id)
    assert (await client.get("/approvals")).status_code == 401
    customer = customer_headers(tenant_x.id)
    assert (
        await client.post(
            f"/approvals/{approval_id}", json={"decision": "approve"}, headers=customer
        )
    ).status_code == 401


async def test_decisions_are_validated(client: AsyncClient, wired, tenant_x):
    _, approval_id = await request_refund(client, tenant_x.id)
    headers = await auth_headers(client, tenant_x)
    for body in ({"decision": "maybe"}, {}, {"decision": "approve", "extra": 1}):
        response = await client.post(f"/approvals/{approval_id}", json=body, headers=headers)
        assert response.status_code == 422


async def test_customers_only_read_their_own_conversations(client: AsyncClient, wired, tenant_x):
    conversation, _ = await request_refund(client, tenant_x.id)
    other = customer_headers(tenant_x.id, "customer-2")
    assert (await client.get(f"/chat/{conversation}", headers=other)).status_code == 404
    assert (await client.get(f"/chat/{uuid.uuid4()}", headers=other)).status_code == 404
    own = await client.get(f"/chat/{conversation}", headers=customer_headers(tenant_x.id))
    assert own.status_code == 200 and own.json()["status"] == "open"


async def test_escalated_conversations_stop_getting_bot_answers(
    client: AsyncClient, wired, tenant_x
):
    _, brain = wired
    brain.route = ["escalate"]
    first = await client.post(
        "/chat", json={"message": "Let me talk to a human"}, headers=customer_headers(tenant_x.id)
    )
    parsed = events(first.text)
    assert "escalated" in [k for k, _ in parsed]
    conversation = next(d for k, d in parsed if k == "meta")["conversation_id"]

    followup = await client.post(
        "/chat",
        json={"message": "Hello?", "conversation_id": conversation},
        headers=customer_headers(tenant_x.id),
    )
    kinds = [k for k, _ in events(followup.text)]
    assert kinds == ["meta", "escalated", "done"]

    admin = await auth_headers(client, tenant_x)
    listed = (await client.get("/conversations", headers=admin)).json()
    status_of = {c["id"]: c["status"] for c in listed}
    assert status_of[conversation] == "escalated"
    assert m.ConversationStatus.ESCALATED.value == "escalated"
