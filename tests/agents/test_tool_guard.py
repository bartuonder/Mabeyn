import uuid
from datetime import timedelta

from httpx import ASGITransport
from sqlalchemy import select

from apps.kargoshop_mock.main import Store, create_app
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.guard.policies import ToolGuard
from mabeyn.guard.settings import save_guard_settings
from mabeyn.tools.executor import HttpToolExecutor, ToolContext
from mabeyn.tools.service import LoadedTool, ToolRunner, load_tools
from tests.agents.harness import Brain, build_env
from tests.fakes import say, tool_call

REFUND = {"order_id": "KS-1001", "amount": 899.90, "reason": "Changed my mind"}


async def security_events(tenant_id: uuid.UUID) -> list[m.SecurityEvent]:
    async with tenant_session(tenant_id) as session:
        return list(await session.scalars(select(m.SecurityEvent)))


async def blocked_run(tenant_id, checkpointer, call, text="Refund it"):
    brain = Brain(route=["action"], tool_turns=[call, say("I could not do that.")])
    env = await build_env(tenant_id, checkpointer, brain, tool_guard=True)
    _, final = await env.ask(text)
    return env, final


async def test_a_refund_above_the_order_total_never_reaches_approval(fresh_tenant, checkpointer):
    call = tool_call("request_refund", **{**REFUND, "amount": 5000})
    env, final = await blocked_run(fresh_tenant, checkpointer, call)

    assert final.status == "answered"
    assert [t["status"] for t in final.trace["tool_trace"]] == ["blocked"]
    assert await env.approvals() == [] and await env.tool_calls() == []
    (event,) = await security_events(fresh_tenant)
    assert (event.layer, event.category) == ("tool", "max_amount")
    assert "larger than the order total" in event.detail["reason"]
    assert "policy:" in env.llm.tool_turns[1][-1]["content"]


async def test_a_refund_for_someone_elses_order_is_blocked(fresh_tenant, checkpointer):
    call = tool_call("request_refund", order_id="KS-2002", amount=100, reason="Not mine")
    env, final = await blocked_run(fresh_tenant, checkpointer, call)

    assert [t["status"] for t in final.trace["tool_trace"]] == ["blocked"]
    assert await env.approvals() == []
    (event,) = await security_events(fresh_tenant)
    assert event.category == "owned_record" and env.store.refunds == []


async def test_changing_the_address_of_someone_elses_order_is_blocked(fresh_tenant, checkpointer):
    call = tool_call(
        "change_address", order_id="KS-2002", line="Evil St 1", city="Ankara", postal_code="06000"
    )
    env, final = await blocked_run(fresh_tenant, checkpointer, call)
    assert [t["status"] for t in final.trace["tool_trace"]] == ["blocked"]
    assert await env.tool_calls() == []


async def test_a_legitimate_refund_still_waits_for_approval(fresh_tenant, checkpointer):
    brain = Brain(route=["action"], tool_turns=[tool_call("request_refund", **REFUND)])
    env = await build_env(fresh_tenant, checkpointer, brain, tool_guard=True)
    _, final = await env.ask("Refund KS-1001")
    assert final.status == "pending_approval" and len(await env.approvals()) == 1
    assert await security_events(fresh_tenant) == []


async def test_write_tools_are_rate_limited_per_customer(fresh_tenant, checkpointer):
    await save_guard_settings(fresh_tenant, {"write_rate_limit": 2})
    ticket = {"subject": "Help", "description": "Please help me."}
    for expected in ("success", "success", "blocked"):
        brain = Brain(
            route=["action"], tool_turns=[tool_call("create_ticket", **ticket), say("ok")]
        )
        env = await build_env(fresh_tenant, checkpointer, brain, tool_guard=True)
        _, final = await env.ask("Open a ticket")
        assert final.trace["tool_trace"][0]["status"] == expected
    assert [e.category for e in await security_events(fresh_tenant)] == ["rate_limit"]

    other = Brain(route=["action"], tool_turns=[tool_call("create_ticket", **ticket), say("ok")])
    env = await build_env(
        fresh_tenant, checkpointer, other, tool_guard=True, customer_id="customer-2"
    )
    _, final = await env.ask("Open a ticket")
    assert final.trace["tool_trace"][0]["status"] == "success"


async def test_reading_is_never_rate_limited(fresh_tenant, checkpointer):
    await save_guard_settings(fresh_tenant, {"write_rate_limit": 0})
    brain = Brain(
        route=["action"], tool_turns=[tool_call("get_order_status", order_id="KS-1002"), say("ok")]
    )
    env = await build_env(fresh_tenant, checkpointer, brain, tool_guard=True)
    _, final = await env.ask("Where is KS-1002?")
    assert final.trace["tool_trace"][0]["status"] == "success"


def spy_on_offered_tools(env) -> list[str]:
    offered: list[str] = []
    original = env.llm.tool_handler

    def spy(messages, tools, model):
        offered.extend(spec["function"]["name"] for spec in tools)
        return original(messages, tools, model)

    env.llm.tool_handler = spy
    return offered


async def test_the_allowlist_hides_other_tools_from_the_model(fresh_tenant, checkpointer):
    await save_guard_settings(fresh_tenant, {"tool_allowlist": ["get_order_status"]})
    brain = Brain(route=["action"], tool_turns=[say("Only lookups here.")])
    env = await build_env(fresh_tenant, checkpointer, brain, tool_guard=True)
    offered = spy_on_offered_tools(env)
    await env.ask("Refund KS-1001")
    assert set(offered) == {"get_order_status"}


async def test_a_disabled_firewall_skips_the_policies(fresh_tenant, checkpointer):
    await save_guard_settings(fresh_tenant, {"firewall_enabled": False})
    call = tool_call("request_refund", **{**REFUND, "amount": 5000})
    brain = Brain(route=["action"], tool_turns=[call])
    env = await build_env(fresh_tenant, checkpointer, brain, tool_guard=True)
    _, final = await env.ask("Refund 5000")
    assert final.status == "pending_approval"


async def test_anonymous_sessions_get_no_customer_tools(fresh_tenant, checkpointer):
    brain = Brain(route=["action"], tool_turns=[say("Please sign in.")])
    env = await build_env(fresh_tenant, checkpointer, brain, anonymous=True)
    offered = spy_on_offered_tools(env)
    await env.ask("Where is my order?")
    assert offered == []


async def test_the_runner_refuses_identity_tools_for_anonymous_callers(fresh_tenant, checkpointer):
    await build_env(fresh_tenant, checkpointer)
    tool = next(t for t in await load_tools(fresh_tenant) if t.name == "get_order_status")
    runner = ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(Store()))))
    context = ToolContext(fresh_tenant, "anon:xyz", anonymous=True)

    refused = await runner.run(context, uuid.uuid4(), tool, {"order_id": "KS-1001"})
    assert not refused.ok and refused.status_code == 403
    allowed = await runner.run(
        ToolContext(fresh_tenant, "customer-1"),
        (await build_env(fresh_tenant, checkpointer)).conversation_id,
        tool,
        {"order_id": "KS-1001"},
    )
    assert allowed.ok


async def test_a_refund_outside_the_return_window_is_blocked_before_any_approval(
    fresh_tenant, checkpointer
):
    call = tool_call("request_refund", order_id="KS-2001", amount=1999.0, reason="Changed my mind")
    brain = Brain(route=["action"], tool_turns=[call, say("The return window has passed.")])
    env = await build_env(
        fresh_tenant, checkpointer, brain, tool_guard=True, customer_id="customer-2"
    )
    _, final = await env.ask("Please refund order KS-2001.")

    assert [t["status"] for t in final.trace["tool_trace"]] == ["blocked"]
    assert await env.approvals() == [] and env.store.refunds == []
    (event,) = await security_events(fresh_tenant)
    assert event.category == "time_window" and "30 day" in event.detail["reason"]
    assert "30 day return window has passed" in env.llm.tool_turns[1][-1]["content"]


async def test_an_order_that_is_not_delivered_cannot_be_refunded(fresh_tenant, checkpointer):
    call = tool_call("request_refund", order_id="KS-1002", amount=2499.0, reason="Too slow")
    env, final = await blocked_run(fresh_tenant, checkpointer, call, "Refund KS-1002")
    assert [t["status"] for t in final.trace["tool_trace"]] == ["blocked"]
    (event,) = await security_events(fresh_tenant)
    assert event.category == "required_status" and "delivered" in event.detail["reason"]


async def test_the_window_ends_thirty_days_after_delivery(fresh_tenant, checkpointer):
    await build_env(fresh_tenant, checkpointer)
    from datetime import date

    tool = next(t for t in await load_tools(fresh_tenant) if t.name == "request_refund")
    runner = ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(Store()))))
    guard = ToolGuard(runner)
    context = ToolContext(fresh_tenant, "customer-2")
    args = {"order_id": "KS-2001", "amount": 10.0, "reason": "late"}
    last_day = date.fromisoformat("2026-08-05") + timedelta(days=30)
    assert await guard.check(context, uuid.uuid4(), tool, args, today=last_day) is None
    late = await guard.check(context, uuid.uuid4(), tool, args, today=last_day + timedelta(days=1))
    assert late is not None and late.policy == "time_window"


async def test_a_tenant_can_attach_policies_to_its_own_tools(fresh_tenant, checkpointer):
    await build_env(fresh_tenant, checkpointer)
    """Policies are data on the tool, so a tool with any name is covered."""
    from mabeyn.guard.policy_specs import MaxAmount, parse_policies

    spec = {
        "type": "max_amount",
        "lookup_tool": "get_order_status",
        "id_argument": "booking",
        "lookup_argument": "order_id",
        "amount_argument": "credit",
        "limit_field": "total",
    }
    assert isinstance(parse_policies([spec])[0], MaxAmount)
    tool = LoadedTool(
        uuid.uuid4(), "issue_credit", "credit", {"type": "object"}, m.RiskLevel.SENSITIVE,
        "POST", "http://shop/credits", True, (spec,),
    )  # fmt: skip
    runner = ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(Store()))))
    guard = ToolGuard(runner)
    context = ToolContext(fresh_tenant, "customer-1")
    over = {"booking": "KS-1001", "credit": 99999.0}
    violation = await guard.check(context, uuid.uuid4(), tool, over)
    assert violation is not None and violation.policy == "max_amount"
    assert await guard.check(context, uuid.uuid4(), tool, {**over, "credit": 5.0}) is None
    foreign = await guard.check(context, uuid.uuid4(), tool, {**over, "booking": "KS-2001"})
    assert foreign is not None


async def test_a_policy_with_a_missing_lookup_tool_blocks_instead_of_passing(fresh_tenant):
    spec = {
        "type": "owned_record",
        "lookup_tool": "no_such_tool",
        "id_argument": "order_id",
    }
    tool = LoadedTool(
        uuid.uuid4(), "cancel_order", "cancel", {"type": "object"}, m.RiskLevel.WRITE,
        "POST", "http://shop/cancel", True, (spec,),
    )  # fmt: skip
    guard = ToolGuard(ToolRunner(HttpToolExecutor()))
    violation = await guard.check(
        ToolContext(fresh_tenant, "customer-1"), uuid.uuid4(), tool, {"order_id": "KS-1001"}
    )
    assert violation is not None and "could not be verified" in violation.reason
