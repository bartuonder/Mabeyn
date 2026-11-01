import json
import uuid
from typing import Any

from langgraph.types import interrupt

from mabeyn.agents import prompts
from mabeyn.agents.state import SupervisorState
from mabeyn.core.config import get_settings
from mabeyn.db.models import Approval, ApprovalStatus, RiskLevel
from mabeyn.db.session import tenant_session
from mabeyn.llm.client import LLM, ChatMessage
from mabeyn.tools.executor import ToolContext, ToolResult
from mabeyn.tools.service import (
    LoadedTool,
    ToolRunner,
    load_tools,
    parse_arguments,
    reject_call,
    start_call,
)

MAX_TOOL_CALLS = 5
MAX_CONSECUTIVE_ERRORS = 3
HISTORY_MESSAGES = 4

# The knowledge agent has no tool access at all; the action agent may use every risk level.
AGENT_RISKS: dict[str, frozenset[RiskLevel]] = {
    "knowledge": frozenset(),
    "action": frozenset(RiskLevel),
}


def _tool_message(call_id: str, result: ToolResult | dict[str, Any]) -> ChatMessage:
    content = result.for_model() if isinstance(result, ToolResult) else json.dumps(result)
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _summary(name: str, arguments: dict[str, Any]) -> str:
    details = ", ".join(f"{key}={value}" for key, value in arguments.items())
    return f"{name}({details})"


class ActionAgent:
    """ReAct loop over the tenant's tools, built from graph nodes so it can pause for approval."""

    def __init__(self, llm: LLM, runner: ToolRunner) -> None:
        self.llm = llm
        self.runner = runner

    def _context(self, state: SupervisorState) -> ToolContext:
        return ToolContext(uuid.UUID(state["tenant_id"]), state["customer_id"])

    async def _tools(self, state: SupervisorState) -> dict[str, LoadedTool]:
        tools = await load_tools(uuid.UUID(state["tenant_id"]), AGENT_RISKS["action"])
        return {t.name: t for t in tools}

    async def start(self, state: SupervisorState) -> dict[str, Any]:
        history = list(state.get("history", []))[-HISTORY_MESSAGES:]
        messages: list[ChatMessage] = [*history, {"role": "user", "content": state["question"]}]
        return {
            "step": state.get("step", 0) + 1,
            "action_messages": messages,
            "action_answer": "",
            "tool_calls_used": 0,
            "tool_errors": 0,
        }

    async def plan(self, state: SupervisorState) -> dict[str, Any]:
        settings = get_settings()
        notes = ""
        if state.get("knowledge_answer"):
            notes = prompts.NOTES_TEMPLATE.format(summary=state["knowledge_answer"])
        system: ChatMessage = {
            "role": "system",
            "content": prompts.ACTION_SYSTEM.format(company=state["company"], notes=notes),
        }
        messages = list(state["action_messages"])

        if state.get("tool_calls_used", 0) >= MAX_TOOL_CALLS:
            budget: ChatMessage = {"role": "system", "content": prompts.TOOL_BUDGET_SYSTEM}
            final = await self.llm.chat([system, *messages, budget], model=settings.llm_model)
            return {"action_messages": messages, "action_answer": final.text.strip()}

        tools = await self._tools(state)
        specs = [tool.spec() for tool in tools.values()]
        turn = await self.llm.chat_with_tools([system, *messages], specs, model=settings.llm_model)
        messages.append(turn.as_assistant_message())
        return {
            "action_messages": messages,
            "action_answer": "" if turn.tool_calls else turn.text.strip(),
        }

    @staticmethod
    def after_plan(state: SupervisorState) -> str:
        last = state["action_messages"][-1]
        return "tools" if last.get("tool_calls") and not state.get("action_answer") else "done"

    async def tools(self, state: SupervisorState) -> dict[str, Any]:
        messages = list(state["action_messages"])
        calls = messages[-1].get("tool_calls", [])
        first, extras = calls[0], calls[1:]
        for extra in extras:
            error = {"ok": False, "error": "only one tool call is allowed per step"}
            messages.append(_tool_message(extra["id"], error))

        name = first["function"]["name"]
        used = state.get("tool_calls_used", 0) + 1
        errors = state.get("tool_errors", 0)
        trace = list(state.get("tool_trace", []))
        tool = (await self._tools(state)).get(name)

        def failed(reason: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
            messages.append(_tool_message(first["id"], {"ok": False, "error": reason}))
            trace.append({"name": name, "arguments": arguments or {}, "status": "invalid"})
            return self._after_call(messages, used, errors + 1, trace)

        if tool is None:
            return failed("unknown tool")
        arguments = parse_arguments(tool, first["function"]["arguments"])
        if isinstance(arguments, str):
            return failed(arguments)

        if tool.risk is RiskLevel.SENSITIVE:
            pending = {
                "name": name,
                "arguments": arguments,
                "tool_call_id": first["id"],
                "summary": _summary(name, arguments),
            }
            return {
                "action_messages": messages,
                "tool_calls_used": used,
                "pending": pending,
                "tool_trace": trace,
            }

        result = await self.runner.run(
            self._context(state), uuid.UUID(state["conversation_id"]), tool, arguments
        )
        messages.append(_tool_message(first["id"], result))
        trace.append(
            {"name": name, "arguments": arguments, "status": "success" if result.ok else "error"}
        )
        return self._after_call(messages, used, 0 if result.ok else errors + 1, trace)

    @staticmethod
    def _after_call(
        messages: list[ChatMessage], used: int, errors: int, trace: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return {
            "action_messages": messages,
            "tool_calls_used": used,
            "tool_errors": errors,
            "tool_trace": trace,
            "needs_human": errors >= MAX_CONSECUTIVE_ERRORS,
            "escalation_reason": "repeated tool failures"
            if errors >= MAX_CONSECUTIVE_ERRORS
            else "",
        }

    @staticmethod
    def after_tools(state: SupervisorState) -> str:
        if state.get("pending"):
            return "approval"
        return "done" if state.get("needs_human") else "plan"

    async def request_approval(self, state: SupervisorState) -> dict[str, Any]:
        pending = dict(state["pending"] or {})
        tenant_id = uuid.UUID(state["tenant_id"])
        conversation_id = uuid.UUID(state["conversation_id"])
        call_id = await start_call(
            tenant_id, conversation_id, pending["name"], pending["arguments"]
        )
        async with tenant_session(tenant_id) as session:
            approval = Approval(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                tool_call_id=call_id,
                status=ApprovalStatus.PENDING,
                payload={
                    "tool": pending["name"],
                    "arguments": pending["arguments"],
                    "customer_id": state["customer_id"],
                    "summary": pending["summary"],
                },
            )
            session.add(approval)
            await session.flush()
            approval_id = approval.id

        notice = await self.llm.chat(
            [
                {
                    "role": "system",
                    "content": prompts.PENDING_SYSTEM.format(summary=pending["summary"]),
                },
                {"role": "user", "content": state["question"]},
            ],
            model=get_settings().llm_model_small,
            max_tokens=150,
        )
        pending.update(call_id=str(call_id), approval_id=str(approval_id))
        trace = [*state.get("tool_trace", []), _pending_trace(pending)]
        return {
            "pending": pending,
            "pending_message": notice.text.strip(),
            "approval_id": str(approval_id),
            "tool_trace": trace,
        }

    async def await_approval(self, state: SupervisorState) -> dict[str, Any]:
        pending = dict(state["pending"] or {})
        # Nothing before this call may have side effects: the node re-runs from the top on resume.
        decision = interrupt(
            {
                "approval_id": pending["approval_id"],
                "tool": pending["name"],
                "arguments": pending["arguments"],
            }
        )
        tenant_id = uuid.UUID(state["tenant_id"])
        messages = list(state["action_messages"])
        trace = list(state.get("tool_trace", []))
        errors = state.get("tool_errors", 0)

        if decision.get("decision") == "approved":
            tool = (await self._tools(state)).get(pending["name"])
            if tool is None:
                outcome: ToolResult | dict[str, Any] = {"ok": False, "error": "tool was removed"}
                status = "error"
            else:
                result = await self.runner.run(
                    self._context(state),
                    uuid.UUID(state["conversation_id"]),
                    tool,
                    pending["arguments"],
                    uuid.UUID(pending["call_id"]),
                )
                outcome, status = result, "success" if result.ok else "error"
            errors = 0 if status == "success" else errors + 1
        else:
            note = str(decision.get("note") or "")
            await reject_call(tenant_id, uuid.UUID(pending["call_id"]), note or "rejected")
            outcome = {"ok": False, "error": "rejected by a human reviewer", "note": note}
            status = "rejected"

        messages.append(_tool_message(pending["tool_call_id"], outcome))
        trace = [t for t in trace if t.get("approval_id") != pending["approval_id"]]
        trace.append({"name": pending["name"], "arguments": pending["arguments"], "status": status})
        return {
            "action_messages": messages,
            "tool_trace": trace,
            "tool_errors": errors,
            "pending": None,
            "pending_message": "",
        }


def _pending_trace(pending: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": pending["name"],
        "arguments": pending["arguments"],
        "status": "pending_approval",
        "approval_id": pending["approval_id"],
    }
