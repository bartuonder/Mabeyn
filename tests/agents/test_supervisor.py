import contextlib

from mabeyn.agents.action import AGENT_RISKS, MAX_TOOL_CALLS
from mabeyn.agents.service import SupervisorService
from mabeyn.db import models as m
from mabeyn.rag.service import Retract, Token
from tests.agents.harness import Brain, StubRag, build_env
from tests.fakes import say, tool_call

REFUND = {"order_id": "KS-1001", "amount": 899.90, "reason": "Changed my mind"}


async def test_knowledge_question_streams_the_answer_without_any_tools(fresh_tenant, checkpointer):
    env = await build_env(fresh_tenant, checkpointer)
    events, final = await env.ask("What is the return policy?")

    assert "".join(e.text for e in events if isinstance(e, Token)) == env.rag.answer
    assert final.status == "answered" and final.answer == env.rag.answer
    assert env.llm.tool_turns == [] and await env.tool_calls() == []


async def test_the_detected_language_reaches_the_knowledge_agent(fresh_tenant, checkpointer):
    env = await build_env(fresh_tenant, checkpointer, Brain(language="English"))
    await env.ask("Do you ship to Germany?")

    assert env.rag.languages == ["English"]


async def test_order_lookup_calls_a_read_tool_for_the_session_customer(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("get_order_status", order_id="KS-1002"), say("It has shipped.")],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    events, final = await env.ask("Where is order KS-1002?")

    assert final.answer == "It has shipped." and final.status == "answered"
    assert [t["status"] for t in final.trace["tool_trace"]] == ["success"]
    (call,) = await env.tool_calls()
    assert call.tool_name == "get_order_status" and call.status is m.ToolCallStatus.SUCCESS
    assert "".join(e.text for e in events if isinstance(e, Token)) == "It has shipped."
    tool_message = env.llm.tool_turns[1][-1]
    assert tool_message["role"] == "tool" and "shipped" in tool_message["content"]


async def test_the_model_cannot_read_another_customers_order(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("get_order_status", order_id="KS-2001"), say("I could not find it.")],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    _, final = await env.ask("Show order KS-2001")

    assert [t["status"] for t in final.trace["tool_trace"]] == ["error"]
    (call,) = await env.tool_calls()
    assert call.status is m.ToolCallStatus.ERROR and call.error == "http 404"


async def test_a_model_supplied_identity_is_rejected(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[
            tool_call("get_order_status", order_id="KS-2001", customer_id="customer-2"),
            say("Sorry."),
        ],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    _, final = await env.ask("Show order KS-2001 for customer-2")
    assert [t["status"] for t in final.trace["tool_trace"]] == ["invalid"]
    assert await env.tool_calls() == []


async def test_a_sensitive_tool_waits_for_approval_and_then_executes(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("request_refund", **REFUND), say("Your refund was submitted.")],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    _, paused = await env.ask("I want a refund for KS-1001")

    assert paused.status == "pending_approval" and paused.answer == "Your request is under review."
    assert paused.approval_id is not None
    assert env.store.refunds == []
    (call,) = await env.tool_calls()
    assert call.status is m.ToolCallStatus.PENDING and call.arguments == REFUND
    (approval,) = await env.approvals()
    assert approval.status is m.ApprovalStatus.PENDING and approval.id == paused.approval_id
    assert (
        approval.payload["customer_id"] == "customer-1"
        and approval.payload["tool"] == "request_refund"
    )

    done = await env.service.resume(env.turn(), approved=True, note=None)
    assert done.status == "answered" and done.answer == "Your refund was submitted."
    assert [r["order_id"] for r in env.store.refunds] == ["KS-1001"]
    (call,) = await env.tool_calls()
    assert call.status is m.ToolCallStatus.SUCCESS


async def test_a_rejected_sensitive_tool_never_runs(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("request_refund", **REFUND), say("A colleague declined it.")],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    await env.ask("Refund KS-1001 please")

    done = await env.service.resume(env.turn(), approved=False, note="Outside policy")
    assert done.answer == "A colleague declined it." and env.store.refunds == []
    (call,) = await env.tool_calls()
    assert call.status is m.ToolCallStatus.REJECTED
    assert [t["status"] for t in done.trace["tool_trace"]] == ["rejected"]
    assert "Outside policy" in env.llm.tool_turns[-1][-1]["content"]


async def test_a_pending_approval_survives_a_restart(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("request_refund", **REFUND), say("Refund submitted.")],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    await env.ask("Refund KS-1001")

    # A brand new service (as after a process restart) resumes from the stored checkpoint.
    restarted = SupervisorService(env.llm, StubRag(), _runner(env), checkpointer)
    done = await restarted.resume(env.turn(), approved=True, note=None)
    assert done.answer == "Refund submitted." and len(env.store.refunds) == 1


def _runner(env):
    from httpx import ASGITransport

    from apps.kargoshop_mock.main import create_app
    from mabeyn.tools.executor import HttpToolExecutor
    from mabeyn.tools.service import ToolRunner

    return ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(env.store))))


async def test_a_second_message_while_waiting_gets_a_reminder_not_a_new_run(
    fresh_tenant, checkpointer
):
    brain = Brain(route=["action"], tool_turns=[tool_call("request_refund", **REFUND)])
    env = await build_env(fresh_tenant, checkpointer, brain)
    _, first = await env.ask("Refund KS-1001")
    turns_before = len(env.llm.tool_turns)

    events, second = await env.ask("Any news?")
    assert second.status == "pending_approval" and second.approval_id == first.approval_id
    assert second.answer == "Still under review."
    assert len(env.llm.tool_turns) == turns_before and len(await env.approvals()) == 1
    assert any(isinstance(e, Token) for e in events)


async def test_human_handoff_marks_the_conversation(fresh_tenant, checkpointer):
    env = await build_env(fresh_tenant, checkpointer, Brain(route=["escalate"]))
    _, final = await env.ask("I want to talk to a person")

    assert final.status == "escalated" and final.answer == "A colleague will take over."
    conversation = await env.conversation()
    assert conversation.status is m.ConversationStatus.ESCALATED
    assert conversation.escalation_reason == "scripted"
    assert env.llm.tool_turns == [] and env.rag.calls == []


async def test_mixed_questions_pass_the_policy_summary_to_the_action_agent(
    fresh_tenant, checkpointer
):
    brain = Brain(
        route=["knowledge", "action"],
        tool_turns=[
            tool_call("get_order_status", order_id="KS-1001"),
            say("KS-1001 is delivered."),
        ],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    events, final = await env.ask("What is the return policy and can I return KS-1001?")

    assert final.answer == "Merged answer."
    system = env.llm.tool_turns[0][0]["content"]
    assert env.rag.answer in system and "Policy notes" in system
    assert "".join(e.text for e in events if isinstance(e, Token)) == "Merged answer."
    assert not any(isinstance(e, Retract) for e in events)


async def test_the_action_agent_never_receives_document_content(fresh_tenant, checkpointer):
    env = await build_env(fresh_tenant, checkpointer, Brain(route=["knowledge", "action"]))
    await env.ask("Policy and order?")
    snapshot = await env.service.graph.aget_state(env.service._config(env.turn()))
    assert "chunks" not in snapshot.values and "ranked" not in snapshot.values
    assert AGENT_RISKS["knowledge"] == frozenset()
    assert set(AGENT_RISKS["action"]) == set(m.RiskLevel)


async def test_the_tool_budget_is_enforced(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("list_orders", call_id=f"c{i}") for i in range(MAX_TOOL_CALLS + 3)],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    _, final = await env.ask("List my orders over and over")

    assert final.trace["tool_calls_used"] == MAX_TOOL_CALLS
    assert len(await env.tool_calls()) == MAX_TOOL_CALLS
    assert final.answer == "Here is what I found."


async def test_invalid_arguments_are_fed_back_so_the_model_can_retry(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[
            tool_call("get_order_status", order_id="1001"),
            tool_call("get_order_status", call_id="call_2", order_id="KS-1001"),
            say("Delivered."),
        ],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    _, final = await env.ask("Status of 1001")

    assert [t["status"] for t in final.trace["tool_trace"]] == ["invalid", "success"]
    assert "invalid arguments" in env.llm.tool_turns[1][-1]["content"]
    assert final.answer == "Delivered."


async def test_repeated_tool_failures_hand_over_to_a_human(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("no_such_tool", call_id=f"c{i}") for i in range(5)],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    _, final = await env.ask("Do something impossible")

    assert final.status == "escalated"
    conversation = await env.conversation()
    assert conversation.escalation_reason == "repeated tool failures"


async def test_empty_messages_are_blocked_before_routing(fresh_tenant, checkpointer):
    env = await build_env(fresh_tenant, checkpointer)
    _, final = await env.ask("   ​  ")
    assert final.status == "blocked" and final.answer == "Please rephrase your message."
    assert "route" not in env.brain.seen


async def test_each_message_starts_a_clean_turn_in_the_same_thread(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("list_orders"), say("You have three orders."), say("Anything else?")],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    await env.ask("List my orders")
    _, second = await env.ask("Thanks")

    assert second.answer == "Anything else?"
    assert second.trace["tool_calls_used"] == 0 and second.trace["tool_trace"] == []


async def test_mixed_requests_search_the_documents_with_a_clean_policy_question(
    fresh_tenant, checkpointer
):
    brain = Brain(route=["knowledge", "action"], tool_turns=[say("Order noted.")])
    env = await build_env(fresh_tenant, checkpointer, brain)
    await env.ask("I want to return order KS-1001", history=[{"role": "user", "content": "hi"}])
    assert env.rag.calls == ["What are the return rules?"]


async def test_an_abstaining_knowledge_agent_is_not_passed_on(fresh_tenant, checkpointer):
    brain = Brain(
        route=["knowledge", "action"],
        tool_turns=[tool_call("request_refund", **REFUND), say("Refund submitted.")],
    )
    rag = StubRag(answer="I could not find reliable information.", abstain=True)
    env = await build_env(fresh_tenant, checkpointer, brain, rag)
    _, paused = await env.ask("Refund KS-1001")

    assert "Policy notes from the knowledge team" not in env.llm.tool_turns[0][0]["content"]
    assert paused.answer == "Your request is under review."
    done = await env.service.resume(env.turn(), approved=True, note=None)
    assert done.answer == "Refund submitted."
    assert "approved this action" in env.llm.tool_turns[-1][-1]["content"]


async def test_the_detected_language_reaches_every_reply_prompt(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        language="English",
        tool_turns=[tool_call("request_refund", **REFUND)],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    env.llm.handler = brain.chat
    await env.ask("Refund KS-1001 please")

    action_system = env.llm.tool_turns[0][0]["content"]
    assert "Write in English." in action_system
    notice_prompt = next(
        m[0]["content"] for _, m in env.llm.chat_calls if m[0]["content"].startswith("Write one")
    )
    assert "Write in English." in notice_prompt


async def test_unknown_language_falls_back_to_mirroring_the_customer(fresh_tenant, checkpointer):
    env = await build_env(fresh_tenant, checkpointer, Brain(route=["escalate"]))
    await env.ask("Bir insanla konuşmak istiyorum")
    prompt = next(
        m[0]["content"] for _, m in env.llm.chat_calls if m[0]["content"].startswith("Write one")
    )
    assert "Write in the language the customer used" in prompt


async def test_resuming_twice_does_not_refund_twice(fresh_tenant, checkpointer):
    brain = Brain(
        route=["action"],
        tool_turns=[tool_call("request_refund", **REFUND), say("Refund submitted."), say("Again.")],
    )
    env = await build_env(fresh_tenant, checkpointer, brain)
    await env.ask("Refund KS-1001")
    await env.service.resume(env.turn(), approved=True, note=None)
    assert len(env.store.refunds) == 1

    # A second, stale resume on the finished thread must not run the tool again.
    with contextlib.suppress(Exception):  # LangGraph may reject a resume on a finished thread
        await env.service.resume(env.turn(), approved=True, note=None)
    assert len(env.store.refunds) == 1
    assert len([c for c in await env.tool_calls() if c.tool_name == "request_refund"]) == 1
