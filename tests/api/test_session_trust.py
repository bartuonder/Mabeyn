from httpx import AsyncClient

from apps.api.main import app
from mabeyn.agents.runtime import get_agent_service
from mabeyn.auth import tokens
from mabeyn.core.config import get_settings
from mabeyn.core.ratelimit import MemoryRateLimiter
from mabeyn.rag.service import Final, Token
from tests.api.test_api_keys import auth_headers
from tests.api.test_chat import parse_sse
from tests.factories import SeededTenant


async def new_key(client, tenant: SeededTenant, **body) -> dict:
    headers = await auth_headers(client, tenant)
    response = await client.post("/api-keys", json={"name": "k", **body}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def open_session(client: AsyncClient, headers: dict[str, str]):
    response = await client.post("/sessions", json={}, headers=headers)
    return tokens.decode_customer_token(response.json()["token"])


async def session_status(client: AsyncClient, headers: dict[str, str]) -> int:
    return (await client.post("/sessions", json={}, headers=headers)).status_code


async def test_secret_keys_open_named_sessions_only(client: AsyncClient, tenant_a: SeededTenant):
    key = await new_key(client, tenant_a)
    assert key["kind"] == "secret" and key["key"].startswith("mbn_sec_")
    named = await client.post(
        "/sessions", json={"customer_id": "cust-7"}, headers={"X-API-Key": key["key"]}
    )
    claims = tokens.decode_customer_token(named.json()["token"])
    assert (claims.customer_id, claims.anonymous) == ("cust-7", False)
    missing = await client.post("/sessions", json={}, headers={"X-API-Key": key["key"]})
    assert missing.status_code == 403


async def test_publishable_keys_open_anonymous_sessions_only(
    client: AsyncClient, tenant_a: SeededTenant
):
    key = await new_key(client, tenant_a, kind="publishable")
    assert key["key"].startswith("mbn_pub_")
    headers = {"X-API-Key": key["key"]}

    first = tokens.decode_customer_token(
        (await client.post("/sessions", json={}, headers=headers)).json()["token"]
    )
    second = tokens.decode_customer_token(
        (await client.post("/sessions", json={}, headers=headers)).json()["token"]
    )
    assert first.anonymous and first.customer_id.startswith("anon:")
    assert first.customer_id != second.customer_id

    named = await client.post("/sessions", json={"customer_id": "cust-7"}, headers=headers)
    assert named.status_code == 403


async def test_publishable_keys_can_be_limited_to_origins(
    client: AsyncClient, tenant_a: SeededTenant
):
    key = await new_key(
        client, tenant_a, kind="publishable", allowed_origins=["https://shop.example"]
    )
    headers = {"X-API-Key": key["key"]}
    ok = await client.post(
        "/sessions", json={}, headers={**headers, "Origin": "https://shop.example"}
    )
    assert ok.status_code == 200
    for origin in ({"Origin": "https://evil.example"}, {}):
        refused = await client.post("/sessions", json={}, headers={**headers, **origin})
        assert refused.status_code == 403


async def test_session_creation_is_rate_limited_per_address_and_per_key(
    client: AsyncClient, tenant_a: SeededTenant, rate_limiter: MemoryRateLimiter, monkeypatch
):
    monkeypatch.setenv("SESSION_RATE_PER_ADDRESS", "3")
    monkeypatch.setenv("SESSION_RATE_PER_KEY", "2")
    get_settings.cache_clear()
    try:
        key = await new_key(client, tenant_a, kind="publishable")
        headers = {"X-API-Key": key["key"]}
        codes = [
            (await client.post("/sessions", json={}, headers=headers)).status_code for _ in range(3)
        ]
        assert codes == [200, 200, 429]
        rate_limiter._hits.clear()
        other = await new_key(client, tenant_a, kind="publishable")
        codes = [
            (
                await client.post("/sessions", json={}, headers={"X-API-Key": other["key"]})
            ).status_code
            for _ in range(4)
        ]
        assert codes == [200, 200, 429, 429]
    finally:
        get_settings.cache_clear()


async def test_anonymous_chat_leaves_no_memories_and_gets_no_customer_tools(
    client: AsyncClient, fresh_tenant, memory_service
):
    seen = {}

    class Agent:
        async def run(self, turn, question, history):
            seen["anonymous"] = turn.anonymous
            yield Token("hello")
            yield Final(answer="hello", citations=[], chunks=[])

    app.dependency_overrides[get_agent_service] = lambda: Agent()
    try:
        token = tokens.create_customer_token(fresh_tenant, "anon:xyz", anonymous=True)
        response = await client.post(
            "/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer {token}"}
        )
    finally:
        app.dependency_overrides.pop(get_agent_service, None)
    conversation = parse_sse(response.text)[0][1]["conversation_id"]
    closed = await client.post(
        f"/chat/{conversation}/close", headers={"Authorization": f"Bearer {token}"}
    )
    assert seen["anonymous"] is True and closed.json()["memories_saved"] == 0


async def test_a_tool_can_be_marked_as_not_needing_identity(
    client: AsyncClient, tenant_a: SeededTenant
):
    headers = await auth_headers(client, tenant_a)
    body = {
        "name": "store_hours",
        "description": "Opening hours of the shop.",
        "json_schema": {
            "type": "object",
            "properties": {"day": {"type": "string"}},
            "additionalProperties": False,
        },
        "risk_level": "read",
        "http_method": "GET",
        "url_template": "http://shop.example/hours",
        "requires_identity": False,
    }
    created = await client.post("/tools", json=body, headers=headers)
    assert created.status_code == 201 and created.json()["requires_identity"] is False
    await client.delete(f"/tools/{created.json()['id']}", headers=headers)
