from decimal import Decimal

from httpx import AsyncClient

from apps.api.main import app
from mabeyn.agents.runtime import get_agent_service
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.rag.service import Final, Token
from tests.api.test_api_keys import auth_headers
from tests.api.test_chat import auth
from tests.factories import SeededTenant


class MeteredAgent:
    def __init__(self, cost: str = "0.002", cached: bool = False):
        self.cost, self.cached = cost, cached

    async def run(self, turn, question, history):
        yield Token("ok")
        yield Final(
            answer="ok",
            citations=[],
            chunks=[],
            usage={
                "input_tokens": 1200,
                "output_tokens": 300,
                "llm_calls": 4,
                "cost_usd": float(self.cost),
                "latency_ms": 2500,
                "cached": self.cached,
            },
        )


async def chat_once(client: AsyncClient, tenant_id, agent) -> None:
    app.dependency_overrides[get_agent_service] = lambda: agent
    try:
        response = await client.post("/chat", json={"message": "hi"}, headers=auth(tenant_id))
        assert response.status_code == 200
    finally:
        app.dependency_overrides.pop(get_agent_service, None)


async def test_reply_usage_is_stored_with_the_message(client: AsyncClient, fresh_tenant):
    await chat_once(client, fresh_tenant, MeteredAgent("0.0031"))
    async with tenant_session(fresh_tenant) as session:
        from sqlalchemy import select

        row = await session.scalar(
            select(m.Message).where(m.Message.role == m.MessageRole.ASSISTANT)
        )
    assert row is not None
    assert (row.input_tokens, row.output_tokens, row.llm_calls) == (1200, 300, 4)
    assert row.cost_usd == Decimal("0.003100") and row.latency_ms == 2500 and not row.cached


async def test_daily_usage_adds_up_per_tenant(
    client: AsyncClient, tenant_x: SeededTenant, tenant_y
):
    await chat_once(client, tenant_x.id, MeteredAgent("0.002"))
    await chat_once(client, tenant_x.id, MeteredAgent("0.001", cached=True))
    admin = await auth_headers(client, tenant_x)

    report = (await client.get("/usage?days=3", headers=admin)).json()
    assert report["total_messages"] == 2 and report["total_cost_usd"] == 0.003
    (today,) = report["days"]
    assert today["messages"] == 2 and today["input_tokens"] == 2400 and today["llm_calls"] == 8
    assert today["cached_messages"] == 1 and today["average_latency_ms"] == 2500

    other = (await client.get("/usage", headers=await auth_headers(client, tenant_y))).json()
    assert other["total_messages"] == 0 and other["days"] == []


async def test_usage_needs_an_admin_and_a_sensible_window(client: AsyncClient, tenant_a):
    assert (await client.get("/usage")).status_code == 401
    admin = await auth_headers(client, tenant_a)
    assert (await client.get("/usage?days=0", headers=admin)).status_code == 422
    assert (await client.get("/usage?days=91", headers=admin)).status_code == 422
