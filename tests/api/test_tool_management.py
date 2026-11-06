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


def refund_payload(**policy):
    spec = {
        "type": "max_amount",
        "lookup_tool": "get_order_status",
        "id_argument": "order_id",
        "amount_argument": "amount",
        "limit_field": "total",
        **policy,
    }
    schema = {
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "pattern": "^KS-[0-9]+$"},
            "amount": {"type": "number"},
        },
        "required": ["order_id", "amount"],
    }
    return payload(
        name="issue_credit",
        json_schema=schema,
        risk_level="sensitive",
        http_method="POST",
        url_template="http://shop/orders/{order_id}/credit",
        policies=[spec],
    )


async def test_a_tool_can_carry_argument_policies(client: AsyncClient, tenant_x):
    headers = await auth_headers(client, tenant_x)
    assert (await client.post("/tools", json=payload(), headers=headers)).status_code == 201
    created = await client.post("/tools", json=refund_payload(), headers=headers)
    assert created.status_code == 201
    assert created.json()["policies"][0]["type"] == "max_amount"


async def test_policies_must_name_a_read_tool_and_real_arguments(client: AsyncClient, tenant_x):
    headers = await auth_headers(client, tenant_x)
    assert (await client.post("/tools", json=payload(), headers=headers)).status_code == 201
    bad = [
        refund_payload(lookup_tool="no_such_tool"),
        refund_payload(id_argument="booking"),
        refund_payload(amount_argument="credit"),
        refund_payload(type="shell_command"),
    ]
    for case in bad:
        response = await client.post("/tools", json=case, headers=headers)
        assert response.status_code == 422, case
