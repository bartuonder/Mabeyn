import uuid

import pytest
from httpx import ASGITransport
from sqlalchemy import select

from apps.kargoshop_mock.main import Store, create_app
from mabeyn.core.config import get_settings
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.tools.executor import HttpToolExecutor, ToolContext
from mabeyn.tools.schema import ToolSchemaError
from mabeyn.tools.service import (
    ToolRunner,
    load_tools,
    parse_arguments,
    start_call,
    validate_definition,
)

ORDER_SCHEMA = {
    "type": "object",
    "properties": {"order_id": {"type": "string", "pattern": "^KS-[0-9]+$"}},
    "required": ["order_id"],
}


def test_a_valid_definition_passes():
    validate_definition("get_order_status", ORDER_SCHEMA, "GET", "http://shop/orders/{order_id}")


@pytest.mark.parametrize(
    ("name", "schema", "method", "url"),
    [
        ("Bad Name", ORDER_SCHEMA, "GET", "http://shop/orders/{order_id}"),
        ("get_order", ORDER_SCHEMA, "TRACE", "http://shop/orders/{order_id}"),
        ("get_order", ORDER_SCHEMA, "GET", "file:///etc/passwd"),
        ("get_order", ORDER_SCHEMA, "GET", "/relative/{order_id}"),
        ("get_order", ORDER_SCHEMA, "GET", "http://shop/orders/{missing}"),
        ("get_order", ORDER_SCHEMA, "GET", "http://shop/{customer_id}/orders"),
        ("get_order", {"type": "object", "properties": {"order_id": {"type": "string"}}}, "GET",
         "http://shop/orders/{order_id}"),  # path parameter must be required
        ("get_order", {"type": "object", "properties": {"customer_id": {"type": "string"}}}, "GET",
         "http://shop/orders"),
    ],
)  # fmt: skip
def test_unsafe_definitions_are_rejected(name, schema, method, url):
    with pytest.raises(ToolSchemaError):
        validate_definition(name, schema, method, url)


async def add_tool(tenant_id, name, risk, schema=ORDER_SCHEMA, url="http://shop/orders/{order_id}"):
    async with tenant_session(tenant_id) as s:
        row = m.ToolDefinition(
            tenant_id=tenant_id,
            name=name,
            description=f"{name} tool",
            json_schema=schema,
            risk_level=risk,
            http_method="GET",
            url_template=url,
        )
        s.add(row)
        await s.flush()
        return row.id


async def test_tools_load_per_tenant_and_filter_by_risk(fresh_tenant, tenant_a):
    await add_tool(fresh_tenant, "get_order_status", m.RiskLevel.READ)
    await add_tool(fresh_tenant, "request_refund", m.RiskLevel.SENSITIVE)

    everything = await load_tools(fresh_tenant)
    assert {t.name for t in everything} == {"get_order_status", "request_refund"}
    read_only = await load_tools(fresh_tenant, frozenset({m.RiskLevel.READ}))
    assert [t.name for t in read_only] == ["get_order_status"]
    foreign = {t.name for t in await load_tools(tenant_a.id)}
    assert "get_order_status" not in foreign and "request_refund" not in foreign


async def test_arguments_are_validated_against_the_registered_schema(fresh_tenant):
    await add_tool(fresh_tenant, "get_order_status", m.RiskLevel.READ)
    (tool,) = await load_tools(fresh_tenant)
    assert parse_arguments(tool, '{"order_id": "KS-1001"}') == {"order_id": "KS-1001"}
    assert "invalid arguments" in str(parse_arguments(tool, '{"order_id": "x"}'))
    assert "invalid arguments" in str(
        parse_arguments(tool, '{"order_id": "KS-1", "customer_id": "z"}')
    )
    assert parse_arguments(tool, "not json") == "arguments are not valid JSON"
    assert parse_arguments(tool, "[1]") == "arguments must be a JSON object"


@pytest.fixture
def shop_runner(monkeypatch):
    monkeypatch.setenv("TOOL_ALLOWED_HOSTS", "shop")
    get_settings.cache_clear()
    yield ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(Store()))))
    monkeypatch.delenv("TOOL_ALLOWED_HOSTS")
    get_settings.cache_clear()


async def conversation(tenant_id: uuid.UUID) -> uuid.UUID:
    async with tenant_session(tenant_id) as s:
        row = m.Conversation(tenant_id=tenant_id, customer_id="customer-1")
        s.add(row)
        await s.flush()
        return row.id


async def audit(tenant_id, call_id) -> m.ToolCall:
    async with tenant_session(tenant_id) as s:
        row = await s.scalar(select(m.ToolCall).where(m.ToolCall.id == call_id))
        assert row is not None
        return row


async def test_runner_executes_and_audits_a_successful_call(fresh_tenant, shop_runner):
    await add_tool(fresh_tenant, "get_order_status", m.RiskLevel.READ)
    (tool,) = await load_tools(fresh_tenant)
    conv = await conversation(fresh_tenant)
    context = ToolContext(fresh_tenant, "customer-1")

    call_id = await start_call(fresh_tenant, conv, tool.name, {"order_id": "KS-1001"})
    result = await shop_runner.run(context, conv, tool, {"order_id": "KS-1001"}, call_id)

    row = await audit(fresh_tenant, call_id)
    assert result.ok and row.status is m.ToolCallStatus.SUCCESS
    assert row.arguments == {"order_id": "KS-1001"} and row.duration_ms is not None
    assert "delivered" in row.result["data"]


async def test_runner_audits_failures_and_blocks_other_customers_data(fresh_tenant, shop_runner):
    await add_tool(fresh_tenant, "get_order_status", m.RiskLevel.READ)
    (tool,) = await load_tools(fresh_tenant)
    conv = await conversation(fresh_tenant)
    context = ToolContext(fresh_tenant, "customer-1")

    result = await shop_runner.run(context, conv, tool, {"order_id": "KS-2001"})
    assert not result.ok and result.status_code == 404
    async with tenant_session(fresh_tenant) as s:
        row = await s.scalar(select(m.ToolCall).where(m.ToolCall.conversation_id == conv))
    assert row is not None and row.status is m.ToolCallStatus.ERROR and row.error == "http 404"
