import uuid

import pytest
from httpx import AsyncClient

from apps.api.main import app
from mabeyn.agents.runtime import get_agent_service
from mabeyn.auth import tokens
from mabeyn.core.config import get_settings
from mabeyn.rag.service import Final, Token
from tests.api.test_api_keys import auth_headers


class QuickAgent:
    async def run(self, turn, question, history):
        yield Token("ok")
        yield Final(answer="ok", citations=[], chunks=[])


@pytest.fixture
def quick_agent():
    app.dependency_overrides[get_agent_service] = lambda: QuickAgent()
    yield
    app.dependency_overrides.pop(get_agent_service, None)


@pytest.fixture
def limits(monkeypatch):
    def set_limits(customer=100, key=100, tenant=100):
        monkeypatch.setenv("CHAT_RATE_PER_CUSTOMER", str(customer))
        monkeypatch.setenv("CHAT_RATE_PER_KEY", str(key))
        monkeypatch.setenv("CHAT_RATE_PER_TENANT", str(tenant))
        get_settings.cache_clear()

    yield set_limits
    get_settings.cache_clear()


def headers(tenant_id, customer: str = "cust-1", key_id: uuid.UUID | None = None) -> dict[str, str]:
    token = tokens.create_customer_token(tenant_id, customer, key_id=key_id)
    return {"Authorization": f"Bearer {token}"}


async def send(client: AsyncClient, request_headers: dict[str, str]):
    return await client.post("/chat", json={"message": "hi"}, headers=request_headers)


async def test_one_customer_cannot_flood_the_chat(client, fresh_tenant, quick_agent, limits):
    limits(customer=2)
    codes = [(await send(client, headers(fresh_tenant))).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    blocked = await send(client, headers(fresh_tenant))
    assert blocked.headers["retry-after"] == "60" and "slow down" in blocked.json()["detail"]
    assert (await send(client, headers(fresh_tenant, "cust-2"))).status_code == 200


async def test_customers_of_one_api_key_share_its_allowance(
    client, fresh_tenant, quick_agent, limits
):
    limits(customer=10, key=3)
    key = uuid.uuid4()
    codes = [
        (await send(client, headers(fresh_tenant, f"cust-{i}", key))).status_code for i in range(4)
    ]
    assert codes == [200, 200, 200, 429]
    other_key = await send(client, headers(fresh_tenant, "cust-9", uuid.uuid4()))
    assert other_key.status_code == 200


async def test_a_tenant_has_an_allowance_of_its_own(
    client, tenant_x, tenant_y, quick_agent, limits
):
    limits(customer=10, key=10, tenant=3)
    codes = [(await send(client, headers(tenant_x.id, f"cust-{i}"))).status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]
    assert (await send(client, headers(tenant_y.id))).status_code == 200


async def test_reading_a_conversation_is_not_limited(client, fresh_tenant, quick_agent, limits):
    limits(customer=1)
    first = await send(client, headers(fresh_tenant))
    conversation = first.text.split('"conversation_id": "')[1].split('"')[0]
    assert (await send(client, headers(fresh_tenant))).status_code == 429
    reading = await client.get(f"/chat/{conversation}", headers=headers(fresh_tenant))
    assert reading.status_code == 200


async def test_sessions_carry_the_key_id_used_for_limits(client: AsyncClient, tenant_a):
    admin = await auth_headers(client, tenant_a)
    created = (await client.post("/api-keys", json={"name": "k"}, headers=admin)).json()
    session = await client.post(
        "/sessions", json={"customer_id": "cust-7"}, headers={"X-API-Key": created["key"]}
    )
    claims = tokens.decode_customer_token(session.json()["token"])
    assert str(claims.key_id) == created["id"]
