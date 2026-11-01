import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from apps.api.main import app
from mabeyn.auth.tokens import create_customer_token
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.rag.service import BaselineRag, get_rag_service
from tests.factories import SeededTenant
from tests.fakes import FakeLLM
from tests.rag.test_retrieval_and_generate import seed


def parse_sse(text: str) -> list[tuple[str, object]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def reply(messages, model) -> str:
    return "Within thirty days [Return Policy:Eligibility]."


@pytest.fixture
def llm():
    fake = FakeLLM(reply)
    app.dependency_overrides[get_rag_service] = lambda: BaselineRag(fake, top_k=3)
    yield fake
    app.dependency_overrides.clear()


def auth(tenant_id: uuid.UUID, customer: str = "cust-1") -> dict[str, str]:
    return {"Authorization": f"Bearer {create_customer_token(tenant_id, customer)}"}


async def test_chat_streams_tokens_citations_and_persists(client: AsyncClient, fresh_tenant, llm):
    await seed(fresh_tenant, llm)
    response = await client.post(
        "/chat", json={"message": "How many days to return?"}, headers=auth(fresh_tenant)
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    kinds = [kind for kind, _ in events]
    assert kinds[0] == "meta" and kinds[-1] == "done"
    assert "token" in kinds and "citations" in kinds
    streamed = "".join(data["text"] for kind, data in events if kind == "token")
    assert streamed.strip() == "Within thirty days [Return Policy:Eligibility]."
    citations = next(data for kind, data in events if kind == "citations")
    assert citations[0]["document_title"] == "Return Policy"

    conversation_id = uuid.UUID(events[0][1]["conversation_id"])
    async with tenant_session(fresh_tenant) as s:
        messages = (
            await s.scalars(
                select(m.Message)
                .where(m.Message.conversation_id == conversation_id)
                .order_by(m.Message.created_at)
            )
        ).all()
    assert [msg.role for msg in messages] == [m.MessageRole.USER, m.MessageRole.ASSISTANT]
    assert messages[0].content == "How many days to return?"
    assert messages[1].citations[0]["cite"] == "Return Policy:Eligibility"


async def test_follow_up_reuses_conversation_and_sends_history(
    client: AsyncClient, fresh_tenant, llm
):
    await seed(fresh_tenant, llm)
    headers = auth(fresh_tenant)
    first = parse_sse((await client.post("/chat", json={"message": "q1"}, headers=headers)).text)
    conversation_id = first[0][1]["conversation_id"]
    await client.post(
        "/chat", json={"message": "q2", "conversation_id": conversation_id}, headers=headers
    )
    final_prompt = llm.chat_calls[-1][1]
    contents = [msg["content"] for msg in final_prompt]
    assert "q1" in contents
    assert any("Within thirty days" in c for c in contents)


async def test_customer_cannot_use_someone_elses_conversation(
    client: AsyncClient, fresh_tenant, llm
):
    await seed(fresh_tenant, llm)
    first = parse_sse(
        (await client.post("/chat", json={"message": "hi"}, headers=auth(fresh_tenant))).text
    )
    conversation_id = first[0][1]["conversation_id"]
    response = await client.post(
        "/chat",
        json={"message": "hijack", "conversation_id": conversation_id},
        headers=auth(fresh_tenant, "someone-else"),
    )
    assert response.status_code == 404


async def test_token_of_another_tenant_cannot_open_foreign_conversation(
    client: AsyncClient, fresh_tenant, tenant_a: SeededTenant, llm
):
    response = await client.post(
        "/chat",
        json={"message": "hi", "conversation_id": str(tenant_a.conversation_id)},
        headers=auth(fresh_tenant, "customer-A"),
    )
    assert response.status_code == 404


async def test_chat_requires_a_customer_token(client: AsyncClient, tenant_a, llm):
    assert (await client.post("/chat", json={"message": "hi"})).status_code == 401
    from mabeyn.auth.tokens import create_access_token

    admin_token = create_access_token(tenant_a.admin_id, tenant_a.id)
    response = await client.post(
        "/chat", json={"message": "hi"}, headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "payload", [{"message": ""}, {"message": "x" * 2001}, {"message": "a", "x": 1}]
)
async def test_chat_validates_input(client: AsyncClient, fresh_tenant, llm, payload):
    response = await client.post("/chat", json=payload, headers=auth(fresh_tenant))
    assert response.status_code == 422


async def test_failures_are_reported_as_error_events_without_details(
    client: AsyncClient, fresh_tenant
):
    class Exploding:
        async def run(self, *args, **kwargs):
            raise RuntimeError("database password is hunter2")
            yield  # pragma: no cover

    app.dependency_overrides[get_rag_service] = lambda: Exploding()
    try:
        response = await client.post("/chat", json={"message": "hi"}, headers=auth(fresh_tenant))
    finally:
        app.dependency_overrides.clear()
    events = parse_sse(response.text)
    assert events[-1][0] == "error"
    assert "hunter2" not in response.text
