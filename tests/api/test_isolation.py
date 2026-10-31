import uuid

import pytest
from httpx import AsyncClient

from tests.api.test_api_keys import auth_headers
from tests.factories import SeededTenant


async def test_admin_lists_only_own_resources(
    client: AsyncClient, tenant_a: SeededTenant, tenant_b: SeededTenant
):
    headers = await auth_headers(client, tenant_a)
    documents = (await client.get("/documents", headers=headers)).json()
    conversations = (await client.get("/conversations", headers=headers)).json()
    tools = (await client.get("/tools", headers=headers)).json()

    assert {d["id"] for d in documents} == {str(tenant_a.document_id)}
    assert {c["id"] for c in conversations} == {str(tenant_a.conversation_id)}
    assert {t["id"] for t in tools} == {str(tenant_a.tool_id)}


@pytest.mark.parametrize(
    ("path", "attr"),
    [
        ("/documents", "document_id"),
        ("/conversations", "conversation_id"),
        ("/tools", "tool_id"),
    ],
)
async def test_admin_cannot_read_other_tenants_resource_by_id(
    client: AsyncClient, tenant_a: SeededTenant, tenant_b: SeededTenant, path: str, attr: str
):
    headers = await auth_headers(client, tenant_a)
    own = await client.get(f"{path}/{getattr(tenant_a, attr)}", headers=headers)
    foreign = await client.get(f"{path}/{getattr(tenant_b, attr)}", headers=headers)
    missing = await client.get(f"{path}/{uuid.uuid4()}", headers=headers)

    assert own.status_code == 200
    # Foreign ids are indistinguishable from ids that do not exist.
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


async def test_conversation_detail_contains_only_own_messages(
    client: AsyncClient, tenant_a: SeededTenant, tenant_b: SeededTenant
):
    headers = await auth_headers(client, tenant_a)
    body = (await client.get(f"/conversations/{tenant_a.conversation_id}", headers=headers)).json()
    assert [m["content"] for m in body["messages"]] == ["hello"]


async def test_tokens_stay_bound_to_their_tenant(
    client: AsyncClient, tenant_a: SeededTenant, tenant_b: SeededTenant
):
    a_headers = await auth_headers(client, tenant_a)
    b_headers = await auth_headers(client, tenant_b)
    a_docs = {d["id"] for d in (await client.get("/documents", headers=a_headers)).json()}
    b_docs = {d["id"] for d in (await client.get("/documents", headers=b_headers)).json()}
    assert a_docs.isdisjoint(b_docs)


async def test_resource_endpoints_require_authentication(client: AsyncClient):
    for path in ("/documents", "/conversations", "/tools"):
        assert (await client.get(path)).status_code == 401
