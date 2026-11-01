from httpx import AsyncClient

from tests.api.test_api_keys import auth_headers

SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string", "pattern": "^KS-[0-9]+$"}},
    "required": ["order_id"],
}


def payload(**overrides):
    base = {
        "name": "get_order_status",
        "description": "Look up one order",
        "json_schema": SCHEMA,
        "risk_level": "read",
        "http_method": "GET",
        "url_template": "http://shop/orders/{order_id}",
    }
    return {**base, **overrides}


async def test_tools_are_created_listed_and_deleted(client: AsyncClient, tenant_x):
    headers = await auth_headers(client, tenant_x)
    created = await client.post("/tools", json=payload(), headers=headers)
    assert created.status_code == 201
    body = created.json()
    assert body["risk_level"] == "read" and body["name"] == "get_order_status"

    listed = (await client.get("/tools", headers=headers)).json()
    assert "get_order_status" in {t["name"] for t in listed}
    assert (await client.delete(f"/tools/{body['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/tools/{body['id']}", headers=headers)).status_code == 404


async def test_duplicate_names_conflict(client: AsyncClient, tenant_x):
    headers = await auth_headers(client, tenant_x)
    assert (await client.post("/tools", json=payload(), headers=headers)).status_code == 201
    assert (await client.post("/tools", json=payload(), headers=headers)).status_code == 409


async def test_unsafe_definitions_are_rejected(client: AsyncClient, tenant_x):
    headers = await auth_headers(client, tenant_x)
    bad_schema = {"type": "object", "properties": {"customer_id": {"type": "string"}}}
    cases = [
        payload(name="Bad Name"),
        payload(json_schema=bad_schema, url_template="http://shop/orders"),
        payload(url_template="file:///etc/passwd"),
        payload(url_template="http://shop/orders/{missing}"),
    ]
    for case in cases:
        response = await client.post("/tools", json=case, headers=headers)
        assert response.status_code == 422, case


async def test_unknown_risk_levels_and_methods_are_rejected(client: AsyncClient, tenant_x):
    headers = await auth_headers(client, tenant_x)
    assert (
        await client.post("/tools", json=payload(risk_level="dangerous"), headers=headers)
    ).status_code == 422
    assert (
        await client.post("/tools", json=payload(http_method="TRACE"), headers=headers)
    ).status_code == 422


async def test_tools_cannot_be_managed_across_tenants(client: AsyncClient, tenant_x, tenant_y):
    x_headers = await auth_headers(client, tenant_x)
    y_headers = await auth_headers(client, tenant_y)
    created = (await client.post("/tools", json=payload(), headers=x_headers)).json()
    assert (await client.delete(f"/tools/{created['id']}", headers=y_headers)).status_code == 404
    assert (await client.post("/tools", json=payload())).status_code == 401
