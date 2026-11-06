import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from mabeyn.agents import prompts
from mabeyn.agents.action import ActionAgent
from mabeyn.agents.state import SupervisorState, fresh_turn
from mabeyn.cache.answers import AnswerCache
from mabeyn.core.config import get_settings
from mabeyn.db.models import Conversation, ConversationStatus
from mabeyn.db.session import tenant_session
from mabeyn.guard.basic import check_input, check_output
from mabeyn.guard.firewall import Firewall
from mabeyn.guard.policies import ToolGuard
from mabeyn.llm.client import LLM, ChatMessage, LLMError, chat_json
from mabeyn.memory.recall import recall
from mabeyn.memory.store import MemoryStore
from mabeyn.rag.generate import safe_fallback
from mabeyn.rag.service import Final, RagService, Retract, Token
from mabeyn.tools.service import ToolRunner

log = structlog.get_logger()

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
    memory: MemoryStore | None = None
    firewall: Firewall | None = None
    tool_guard: ToolGuard | None = None
    cache: AnswerCache | None = None


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
    firewall = deps.firewall
    action = ActionAgent(
        llm,
        deps.runner,
        canary_guard=bool(firewall and firewall.config.output),
        guard=deps.tool_guard,
    )

    async def input_guard(state: SupervisorState) -> dict[str, Any]:
        tenant_id = uuid.UUID(state["tenant_id"])
        decision = check_input(state["question"])
        update: dict[str, Any] = {**fresh_turn(), "question": decision.text}
        category = "" if decision.allowed else "invalid_input"
        if decision.allowed and firewall is not None:
            verdict = await firewall.inspect_input(
                tenant_id,
                uuid.UUID(state["conversation_id"]),
                state["question"],
                decision.text,
                [m["content"] for m in state.get("history", []) if m["role"] == "user"],
            )
            category = "" if verdict.allowed else (verdict.category or "blocked")
        if category:
            update.update(
                blocked=True,
                block_category=category,
                answer=prompts.BLOCKED_REPLY,
                status="blocked",
            )
        if firewall is not None:
            # Replies are checked before the customer sees them, so nothing streams ahead of it.
            update["hold_stream"] = await firewall.active(tenant_id, "output")
        return update

    def after_input_guard(state: SupervisorState) -> str:
        return "output_guard" if state.get("blocked") else "cache_lookup"

    def first_turn(state: SupervisorState) -> bool:
        return not state.get("history") and not state.get("summary")

    async def cache_lookup(state: SupervisorState) -> dict[str, Any]:
        """Answer a repeated first question from the cache, after the input checks passed."""
        if deps.cache is None or not get_settings().cache_enabled or not first_turn(state):
            return {}
        try:
            hit = await deps.cache.lookup(uuid.UUID(state["tenant_id"]), state["question"])
        except Exception:
            # The cache only saves money; a failing one must never stop the conversation.
            log.exception("cache_lookup_failed")
            return {}
        if hit is None:
            return {}
        if not state.get("hold_stream"):
            get_stream_writer()({"type": "token", "text": hit.answer})
        return {
            "cached": True,
            "plan": ["knowledge"],
            "answer": hit.answer,
            "knowledge_answer": hit.answer,
            "knowledge_citations": hit.citations,
            "status": "answered",
        }

    def after_cache_lookup(state: SupervisorState) -> str:
        return "output_guard" if state.get("cached") else "recall_memories"

    async def cache_store(state: SupervisorState) -> dict[str, Any]:
        """Keep a plain knowledge answer; personal or tool related replies are never kept."""
        eligible = (
            deps.cache is not None
            and get_settings().cache_enabled
            and first_turn(state)
            and not state.get("cached")
            and not state.get("blocked")
            and state.get("status") == "answered"
            and state.get("plan") == ["knowledge"]
            and not state.get("knowledge_abstained")
            and state.get("answer")
            and state.get("answer") == state.get("knowledge_answer")
        )
        if eligible and deps.cache is not None:
            try:
                await deps.cache.store(
                    uuid.UUID(state["tenant_id"]),
                    state["question"],
                    state["answer"],
                    state.get("knowledge_citations", []),
                )
            except Exception:
                log.exception("cache_store_failed")
        return {}

    async def recall_memories(state: SupervisorState) -> dict[str, Any]:
        if deps.memory is None or state.get("anonymous"):
            return {"memories": []}
        try:
            notes = await recall(
                deps.memory,
                uuid.UUID(state["tenant_id"]),
                state["customer_id"],
                get_settings().memory_recall_limit,
            )
        except Exception:
            # Memory is a convenience; a failing store must never stop the conversation.
            log.exception("memory_recall_failed")
            notes = []
        return {"memories": notes}

    async def supervisor(state: SupervisorState) -> dict[str, Any]:
        route, reason, knowledge_question, language = ["knowledge"], "default", "", ""
        user = state["question"]
        context = []
        if state.get("summary"):
            context.append(f"Summary of the earlier conversation:\n{state['summary']}")
        if state.get("history"):
            context.append(f"Conversation so far:\n{_history_text(state['history'])}")
        if state.get("memories"):
            context.append(prompts.ROUTER_MEMORY_HINT)
        if context:
            user = "\n\n".join([*context, f"Latest: {user}"])
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
                language = str(data.get("language") or "").strip()
        except (LLMError, AttributeError, TypeError):
            pass
        return {
            "plan": route,
            "route_reason": reason,
            "knowledge_question": knowledge_question,
            "language": language,
        }

    async def dispatch(state: SupervisorState) -> dict[str, Any]:
        return {}

    def route_next(state: SupervisorState) -> str:
        if state.get("needs_human"):
            return "escalate"
        plan, step = state.get("plan", []), state.get("step", 0)
        return NEXT_NODE[plan[step]] if step < len(plan) else "compose"

    async def knowledge_agent(state: SupervisorState) -> dict[str, Any]:
        live = state.get("plan") == ["knowledge"] and not state.get("hold_stream")
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
            state.get("language") or None,
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
        system = prompts.ESCALATE_SYSTEM.format(
            language_rule=prompts.language_rule(state.get("language"))
        )
        message = await _short_reply(llm, system, state["question"])
        async with tenant_session(uuid.UUID(state["tenant_id"])) as session:
            conversation = await session.get(Conversation, uuid.UUID(state["conversation_id"]))
            if conversation is not None:
                conversation.status = ConversationStatus.ESCALATED
                conversation.escalation_reason = reason[:500]
        if not state.get("hold_stream"):
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
                        "content": prompts.COMPOSE_SYSTEM.format(
                            company=state["company"],
                            language_rule=prompts.language_rule(state.get("language")),
                        ),
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
                if not state.get("hold_stream"):
                    writer({"type": "token", "text": piece})
            return {"answer": "".join(pieces).strip()}
        answer = acted or knowledge
        if not answer:
            answer = await safe_fallback(llm, state["question"])
        if not state.get("streamed") and not state.get("hold_stream"):
            writer({"type": "token", "text": answer})
        return {"answer": answer}

    async def output_guard(state: SupervisorState) -> dict[str, Any]:
        text = check_output(state.get("answer", "")).text
        if firewall is not None and not state.get("blocked"):
            decision = await firewall.filter_output(
                uuid.UUID(state["tenant_id"]),
                uuid.UUID(state["conversation_id"]),
                text,
                prompts.LEAK_REPLY,
            )
            text = decision.text
        if state.get("hold_stream") and text:
            get_stream_writer()({"type": "token", "text": text})
        return {"answer": text}

    graph = StateGraph(SupervisorState)
    for name, node in {
        "input_guard": input_guard,
        "cache_lookup": cache_lookup,
        "recall_memories": recall_memories,
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
        "cache_store": cache_store,
    }.items():
        graph.add_node(name, node)

    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges(
        "input_guard",
        after_input_guard,
        {"cache_lookup": "cache_lookup", "output_guard": "output_guard"},
    )
    graph.add_conditional_edges(
        "cache_lookup",
        after_cache_lookup,
        {"recall_memories": "recall_memories", "output_guard": "output_guard"},
    )
    graph.add_edge("recall_memories", "supervisor")
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
    graph.add_edge("output_guard", "cache_store")
    graph.add_edge("cache_store", END)
    return graph
