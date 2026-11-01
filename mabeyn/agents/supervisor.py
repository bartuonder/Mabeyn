import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from mabeyn.agents import prompts
from mabeyn.agents.action import ActionAgent
from mabeyn.agents.state import SupervisorState, fresh_turn
from mabeyn.core.config import get_settings
from mabeyn.db.models import Conversation, ConversationStatus
from mabeyn.db.session import tenant_session
from mabeyn.guard.basic import check_input, check_output
from mabeyn.llm.client import LLM, ChatMessage, LLMError, chat_json
from mabeyn.rag.generate import safe_fallback
from mabeyn.rag.service import Final, RagService, Retract, Token
from mabeyn.tools.service import ToolRunner

VALID_ROUTES = (
    ["knowledge"],
    ["action"],
    ["knowledge", "action"],
    ["escalate"],
)
ROUTER_HISTORY = 4
NEXT_NODE = {"knowledge": "knowledge_agent", "action": "action_start", "escalate": "escalate"}


@dataclass
class AgentDeps:
    llm: LLM
    rag: RagService
    runner: ToolRunner


def _history_text(history: list[ChatMessage]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in history[-ROUTER_HISTORY:])


def _citation_dicts(final: Final) -> list[dict[str, Any]]:
    return [
        {
            "cite": c.cite,
            "document_id": str(c.document_id),
            "document_title": c.document_title,
            "section": c.section,
            "page": c.page,
        }
        for c in final.citations
    ]


async def _short_reply(llm: LLM, system: str, question: str) -> str:
    result = await llm.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": question}],
        model=get_settings().llm_model_small,
        max_tokens=150,
    )
    return result.text.strip()


def build_supervisor_graph(deps: AgentDeps) -> StateGraph[Any, Any, Any, Any]:
    llm = deps.llm
    action = ActionAgent(llm, deps.runner)

    async def input_guard(state: SupervisorState) -> dict[str, Any]:
        decision = check_input(state["question"])
        update: dict[str, Any] = {**fresh_turn(), "question": decision.text}
        if not decision.allowed:
            reply = await _short_reply(llm, prompts.BLOCKED_SYSTEM, state["question"] or "?")
            update.update(blocked=True, answer=reply, status="blocked")
        return update

    def after_input_guard(state: SupervisorState) -> str:
        return "output_guard" if state.get("blocked") else "supervisor"

    async def supervisor(state: SupervisorState) -> dict[str, Any]:
        route, reason, knowledge_question = ["knowledge"], "default", ""
        user = state["question"]
        if state.get("history"):
            user = f"Conversation so far:\n{_history_text(state['history'])}\n\nLatest: {user}"
        try:
            data = await chat_json(
                llm,
                [
                    {
                        "role": "system",
                        "content": prompts.SUPERVISOR_SYSTEM.format(company=state["company"]),
                    },
                    {"role": "user", "content": user},
                ],
                model=get_settings().llm_model_small,
                max_tokens=100,
            )
            if data.get("route") in VALID_ROUTES:
                route, reason = data["route"], str(data.get("reason", ""))
                knowledge_question = str(data.get("knowledge_question") or "").strip()
        except (LLMError, AttributeError, TypeError):
            pass
        return {"plan": route, "route_reason": reason, "knowledge_question": knowledge_question}

    async def dispatch(state: SupervisorState) -> dict[str, Any]:
        return {}

    def route_next(state: SupervisorState) -> str:
        if state.get("needs_human"):
            return "escalate"
        plan, step = state.get("plan", []), state.get("step", 0)
        return NEXT_NODE[plan[step]] if step < len(plan) else "compose"

    async def knowledge_agent(state: SupervisorState) -> dict[str, Any]:
        live = state.get("plan") == ["knowledge"]
        writer = get_stream_writer()
        final: Final | None = None
        mixed = len(state.get("plan", [])) > 1
        # In a mixed request the document search gets a policy question without order details.
        question = (state.get("knowledge_question") if mixed else "") or state["question"]
        async for event in deps.rag.run(
            uuid.UUID(state["tenant_id"]),
            question,
            [] if mixed else state.get("history", []),
            state["company"],
        ):
            if isinstance(event, Token) and live:
                writer({"type": "token", "text": event.text})
            elif isinstance(event, Retract) and live:
                writer({"type": "retract"})
            elif isinstance(event, Final):
                final = event
        if final is None:
            return {"step": state.get("step", 0) + 1}
        # An abstention is only a stock apology; it must not reach the action agent or customer.
        usable = not final.abstained
        return {
            "step": state.get("step", 0) + 1,
            "streamed": live,
            "knowledge_answer": final.answer if usable or live else "",
            "knowledge_citations": _citation_dicts(final) if usable else [],
            "knowledge_abstained": final.abstained,
        }

    async def escalate(state: SupervisorState) -> dict[str, Any]:
        reason = state.get("escalation_reason") or state.get("route_reason") or "requested"
        message = await _short_reply(llm, prompts.ESCALATE_SYSTEM, state["question"])
        async with tenant_session(uuid.UUID(state["tenant_id"])) as session:
            conversation = await session.get(Conversation, uuid.UUID(state["conversation_id"]))
            if conversation is not None:
                conversation.status = ConversationStatus.ESCALATED
                conversation.escalation_reason = reason[:500]
        get_stream_writer()({"type": "token", "text": message})
        return {"answer": message, "status": "escalated", "escalation_reason": reason}

    async def compose(state: SupervisorState) -> dict[str, Any]:
        writer = get_stream_writer()
        knowledge, acted = state.get("knowledge_answer", ""), state.get("action_answer", "")
        if knowledge and acted:
            user = f"Knowledge answer:\n{knowledge}\n\nOrder answer:\n{acted}"
            pieces: list[str] = []
            async for piece in llm.stream(
                [
                    {
                        "role": "system",
                        "content": prompts.COMPOSE_SYSTEM.format(company=state["company"]),
                    },
                    {
                        "role": "user",
                        "content": f"{user}\n\nCustomer message (answer in its language): "
                        f"{state['question']}",
                    },
                ],
                model=get_settings().llm_model,
            ):
                pieces.append(piece)
                writer({"type": "token", "text": piece})
            return {"answer": "".join(pieces).strip()}
        answer = acted or knowledge
        if not answer:
            answer = await safe_fallback(llm, state["question"])
        if not state.get("streamed"):
            writer({"type": "token", "text": answer})
        return {"answer": answer}

    async def output_guard(state: SupervisorState) -> dict[str, Any]:
        return {"answer": check_output(state.get("answer", "")).text}

    graph = StateGraph(SupervisorState)
    for name, node in {
        "input_guard": input_guard,
        "supervisor": supervisor,
        "dispatch": dispatch,
        "knowledge_agent": knowledge_agent,
        "action_start": action.start,
        "action_plan": action.plan,
        "action_tools": action.tools,
        "action_request_approval": action.request_approval,
        "action_await_approval": action.await_approval,
        "escalate": escalate,
        "compose": compose,
        "output_guard": output_guard,
    }.items():
        graph.add_node(name, node)

    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges(
        "input_guard",
        after_input_guard,
        {"supervisor": "supervisor", "output_guard": "output_guard"},
    )
    graph.add_edge("supervisor", "dispatch")
    graph.add_conditional_edges(
        "dispatch",
        route_next,
        {
            "knowledge_agent": "knowledge_agent",
            "action_start": "action_start",
            "escalate": "escalate",
            "compose": "compose",
        },
    )
    graph.add_edge("knowledge_agent", "dispatch")
    graph.add_edge("action_start", "action_plan")
    graph.add_conditional_edges(
        "action_plan", action.after_plan, {"tools": "action_tools", "done": "dispatch"}
    )
    graph.add_conditional_edges(
        "action_tools",
        action.after_tools,
        {"approval": "action_request_approval", "plan": "action_plan", "done": "dispatch"},
    )
    graph.add_edge("action_request_approval", "action_await_approval")
    graph.add_edge("action_await_approval", "action_plan")
    graph.add_edge("escalate", "output_guard")
    graph.add_edge("compose", "output_guard")
    graph.add_edge("output_guard", END)
    return graph
