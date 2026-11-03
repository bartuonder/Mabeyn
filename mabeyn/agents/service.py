import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from sqlalchemy import select

from mabeyn.agents import prompts
from mabeyn.agents.state import SupervisorState
from mabeyn.agents.supervisor import AgentDeps, build_supervisor_graph
from mabeyn.core.config import get_settings
from mabeyn.db.models import Approval, ApprovalStatus
from mabeyn.db.session import tenant_session
from mabeyn.guard.firewall import Firewall
from mabeyn.llm.client import LLM, ChatMessage
from mabeyn.memory.store import MemoryStore
from mabeyn.rag.service import Final, RagEvent, RagService, Retract, Token
from mabeyn.rag.types import Citation
from mabeyn.tools.service import ToolRunner


@dataclass(frozen=True)
class TurnContext:
    tenant_id: uuid.UUID
    customer_id: str
    conversation_id: uuid.UUID
    company: str
    today: date | None = None
    summary: str = ""

    @property
    def thread_id(self) -> str:
        return f"{self.tenant_id}:{self.conversation_id}"


class AgentService(Protocol):
    def run(
        self, turn: TurnContext, question: str, history: Sequence[ChatMessage]
    ) -> AsyncIterator[RagEvent]: ...

    async def resume(self, turn: TurnContext, approved: bool, note: str | None) -> Final: ...


def _citations(items: list[dict[str, Any]]) -> list[Citation]:
    return [
        Citation(
            cite=i["cite"],
            document_id=uuid.UUID(i["document_id"]),
            document_title=i["document_title"],
            section=i["section"],
            page=i["page"],
            chunk_ids=(),
        )
        for i in items
    ]


class SupervisorService:
    """Runs the supervisor graph for one conversation thread and resumes it after approvals."""

    def __init__(
        self,
        llm: LLM,
        rag: RagService,
        runner: ToolRunner,
        checkpointer: BaseCheckpointSaver[Any],
        memory: MemoryStore | None = None,
        firewall: Firewall | None = None,
    ) -> None:
        self.llm = llm
        self.graph: Any = build_supervisor_graph(
            AgentDeps(llm, rag, runner, memory, firewall)
        ).compile(checkpointer=checkpointer)

    @staticmethod
    def _config(turn: TurnContext) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": turn.thread_id},
            "run_name": "mabeyn-supervisor",
            "tags": ["mabeyn-agents"],
            "metadata": {
                "tenant_id": str(turn.tenant_id),
                "prompt_version": prompts.PROMPT_VERSION,
                "model": get_settings().llm_model,
            },
        }

    async def _pending_approval(self, turn: TurnContext) -> uuid.UUID | None:
        async with tenant_session(turn.tenant_id) as session:
            return await session.scalar(
                select(Approval.id).where(
                    Approval.conversation_id == turn.conversation_id,
                    Approval.status == ApprovalStatus.PENDING,
                )
            )

    async def run(
        self, turn: TurnContext, question: str, history: Sequence[ChatMessage]
    ) -> AsyncIterator[RagEvent]:
        waiting = await self._pending_approval(turn)
        if waiting is not None:
            reminder = (
                await self.llm.chat(
                    [
                        {
                            "role": "system",
                            "content": prompts.REMINDER_SYSTEM.format(
                                language_rule=prompts.language_rule(None)
                            ),
                        },
                        {"role": "user", "content": question},
                    ],
                    model=get_settings().llm_model_small,
                    max_tokens=120,
                )
            ).text.strip()
            yield Token(reminder)
            yield Final(
                answer=reminder,
                citations=[],
                chunks=[],
                status="pending_approval",
                approval_id=waiting,
            )
            return

        initial: SupervisorState = {
            "tenant_id": str(turn.tenant_id),
            "customer_id": turn.customer_id,
            "conversation_id": str(turn.conversation_id),
            "company": turn.company,
            "today": (turn.today or date.today()).isoformat(),
            "question": question,
            "history": list(history),
            "summary": turn.summary,
        }
        config = self._config(turn)
        async for mode, payload in self.graph.astream(
            initial,
            config,
            stream_mode=["custom", "values"],
        ):
            if mode == "custom":
                yield Token(payload["text"]) if payload["type"] == "token" else Retract()
        yield await self._final(config)

    async def resume(self, turn: TurnContext, approved: bool, note: str | None) -> Final:
        config = self._config(turn)
        decision = {"decision": "approved" if approved else "rejected", "note": note}
        async for _ in self.graph.astream(
            Command(resume=decision),
            config,
            stream_mode=["values"],
        ):
            pass
        return await self._final(config)

    async def _final(self, config: dict[str, Any]) -> Final:
        snapshot = await self.graph.aget_state(config)
        values: SupervisorState = snapshot.values
        paused = bool(snapshot.next)
        trace = {
            "route": values.get("plan", []),
            "tool_trace": values.get("tool_trace", []),
            "tool_calls_used": values.get("tool_calls_used", 0),
        }
        if paused:
            approval = values.get("approval_id")
            return Final(
                answer=values.get("pending_message", ""),
                citations=[],
                chunks=[],
                trace=trace,
                status="pending_approval",
                approval_id=uuid.UUID(approval) if approval else None,
            )
        return Final(
            answer=values.get("answer", ""),
            citations=_citations(values.get("knowledge_citations", [])),
            chunks=[],
            abstained=bool(values.get("knowledge_abstained")),
            trace=trace,
            status=values.get("status", "answered"),
        )
