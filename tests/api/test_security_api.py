from httpx import AsyncClient
from sqlalchemy import select

from apps.api.main import app
from mabeyn.agents.runtime import get_agent_service
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.guard.events import record_event
from mabeyn.rag.service import Final
from tests.api.test_api_keys import auth_headers
from tests.api.test_chat import auth, parse_sse
from tests.factories import SeededTenant


async def test_admins_read_security_events_and_change_firewall_settings(
    client: AsyncClient, tenant_a: SeededTenant, tenant_b: SeededTenant
):
    admin_a = await auth_headers(client, tenant_a)
    await record_event(tenant_a.id, None, "input", "override", m.SecurityAction.BLOCKED, {"x": 1})
    await record_event(tenant_a.id, None, "output", "pii_masked", m.SecurityAction.FLAGGED)

    events = (await client.get("/security-events", headers=admin_a)).json()
    assert {e["layer"] for e in events} >= {"input", "output"}
    only_input = (await client.get("/security-events?layer=input", headers=admin_a)).json()
    assert only_input and all(e["layer"] == "input" for e in only_input)
    flagged = (await client.get("/security-events?action=flagged", headers=admin_a)).json()
    assert all(e["action"] == "flagged" for e in flagged)
    other = (
        await client.get("/security-events", headers=await auth_headers(client, tenant_b))
    ).json()
    assert all(e["category"] not in {"override", "pii_masked"} for e in other)

    assert (await client.get("/security-events")).status_code == 401
    assert (await client.get("/settings/firewall")).status_code == 401

    default = (await client.get("/settings/firewall", headers=admin_a)).json()
    assert default["injection_threshold"] == 0.7 and default["tool_allowlist"] is None
    updated = await client.put(
        "/settings/firewall",
        json={"injection_threshold": 0.5, "tool_allowlist": ["get_order_status"]},
        headers=admin_a,
    )
    assert updated.json()["injection_threshold"] == 0.5
    assert updated.json()["tool_allowlist"] == ["get_order_status"]
    kept = await client.put("/settings/firewall", json={"write_rate_limit": 3}, headers=admin_a)
    assert (
        kept.json()["tool_allowlist"] == ["get_order_status"]
        and kept.json()["write_rate_limit"] == 3
    )
    cleared = await client.put("/settings/firewall", json={"tool_allowlist": None}, headers=admin_a)
    assert cleared.json()["tool_allowlist"] is None
    for bad in ({"injection_threshold": 2}, {"write_rate_limit": -1}, {"unknown": 1}):
        assert (
            await client.put("/settings/firewall", json=bad, headers=admin_a)
        ).status_code == 422
    await client.put(
        "/settings/firewall",
        json={"injection_threshold": 0.7, "write_rate_limit": 5},
        headers=admin_a,
    )


async def test_blocked_messages_are_marked_and_kept(client: AsyncClient, fresh_tenant):
    class Blocker:
        async def run(self, turn, question, history):
            yield Final(answer="blocked", citations=[], chunks=[], status="blocked")

    app.dependency_overrides[get_agent_service] = lambda: Blocker()
    try:
        response = await client.post(
            "/chat", json={"message": "bad message"}, headers=auth(fresh_tenant)
        )
    finally:
        app.dependency_overrides.pop(get_agent_service, None)
    conversation = parse_sse(response.text)[0][1]["conversation_id"]
    async with tenant_session(fresh_tenant) as session:
        rows = list(
            await session.scalars(select(m.Message).where(m.Message.role == m.MessageRole.USER))
        )
    assert [(r.content, r.blocked) for r in rows] == [("bad message", True)]
    assert conversation
