import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from httpx import ASGITransport
from sqlalchemy import select

from apps.kargoshop_mock.main import Store, create_app
from mabeyn.agents.service import SupervisorService, TurnContext
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.guard.classifier import LlmInjectionClassifier
from mabeyn.guard.firewall import Firewall, FirewallConfig
from mabeyn.guard.policies import ToolGuard
from mabeyn.llm.client import ChatMessage, ToolChatResult
from mabeyn.memory.store import MemoryStore
from mabeyn.rag.service import Final, RagEvent, Token
from mabeyn.seed import register_kargoshop_tools
from mabeyn.tools.executor import HttpToolExecutor
from mabeyn.tools.service import ToolRunner
from tests.fakes import FakeLLM, say

SHOP_URL = "http://shop"


class StubRag:
    """Knowledge agent stand-in: returns a fixed answer without touching documents."""

    def __init__(
        self, answer: str = "Returns are accepted within 30 days.", abstain: bool = False
    ) -> None:
        self.answer = answer
        self.abstain = abstain
        self.calls: list[str] = []
        self.languages: list[str | None] = []

    async def run(
        self,
        tenant_id: uuid.UUID,
        question: str,
        history: Sequence[ChatMessage],
        company: str,
        language: str | None = None,
    ) -> AsyncIterator[RagEvent]:
        self.calls.append(question)
        self.languages.append(language)
        yield Token(self.answer)
        yield Final(answer=self.answer, citations=[], chunks=[], abstained=self.abstain)


@dataclass
class Brain:
    """Scripted model behaviour keyed by the system prompt of each call."""

    route: list[str] = field(default_factory=lambda: ["knowledge"])
    language: str = ""
    tool_turns: list[ToolChatResult] = field(default_factory=list)
    replies: dict[str, str] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)
    guard_score: float = 0.0

    MARKERS = {
        "You route customer messages": "route",
        "security filter in front of": "guard",
        "needs a review by a human colleague": "pending",
        "earlier request is still being reviewed": "reminder",
        "human colleague will take over": "escalate",
        "cannot process this message": "blocked",
        "You merge two partial answers": "compose",
        "You are the order assistant": "action",
        "could not find reliable": "fallback",
    }
    DEFAULTS = {
        "pending": "Your request is under review.",
        "reminder": "Still under review.",
        "escalate": "A colleague will take over.",
        "blocked": "Please rephrase your message.",
        "compose": "Merged answer.",
        "action": "Here is what I found.",
        "fallback": "I could not find that.",
    }

    def kind(self, system: str) -> str:
        return next((k for marker, k in self.MARKERS.items() if marker in system), "other")

    def chat(self, messages: Sequence[ChatMessage], model: str) -> str:
        kind = self.kind(messages[0]["content"])
        self.seen.append(kind)
        if kind == "route":
            question = "What are the return rules?" if len(self.route) > 1 else ""
            return json.dumps(
                {
                    "route": self.route,
                    "reason": "scripted",
                    "knowledge_question": question,
                    "language": self.language,
                }
            )
        if kind == "guard":
            return json.dumps({"score": self.guard_score, "category": "override"})
        return self.replies.get(kind, self.DEFAULTS.get(kind, "ok"))

    def tools(self, messages: Sequence[ChatMessage], tools: Any, model: str) -> ToolChatResult:
        self.seen.append("action_turn")
        return self.tool_turns.pop(0) if self.tool_turns else say("Done.")


@dataclass
class Env:
    tenant_id: uuid.UUID
    conversation_id: uuid.UUID
    store: Store
    llm: FakeLLM
    brain: Brain
    rag: StubRag
    service: SupervisorService
    customer_id: str = "customer-1"
    anonymous: bool = False

    def turn(self, customer_id: str | None = None, summary: str = "") -> TurnContext:
        return TurnContext(
            self.tenant_id,
            customer_id or self.customer_id,
            self.conversation_id,
            "KargoShop",
            summary=summary,
            anonymous=self.anonymous,
        )

    async def ask(
        self, text: str, history: Sequence[ChatMessage] = (), summary: str = ""
    ) -> tuple[list[RagEvent], Final]:
        events = [e async for e in self.service.run(self.turn(summary=summary), text, history)]
        final = next(e for e in events if isinstance(e, Final))
        return events, final

    async def tool_calls(self) -> list[m.ToolCall]:
        async with tenant_session(self.tenant_id) as session:
            rows = await session.scalars(
                select(m.ToolCall)
                .where(m.ToolCall.conversation_id == self.conversation_id)
                .order_by(m.ToolCall.created_at)
            )
            return list(rows)

    async def approvals(self) -> list[m.Approval]:
        async with tenant_session(self.tenant_id) as session:
            return list(await session.scalars(select(m.Approval)))

    async def conversation(self) -> m.Conversation:
        async with tenant_session(self.tenant_id) as session:
            row = await session.get(m.Conversation, self.conversation_id)
            assert row is not None
            return row


async def build_env(
    tenant_id: uuid.UUID,
    checkpointer: Any,
    brain: Brain | None = None,
    rag: StubRag | None = None,
    customer_id: str = "customer-1",
    memory: MemoryStore | None = None,
    firewall: bool | FirewallConfig = False,
    tool_guard: bool = False,
    anonymous: bool = False,
) -> Env:
    brain = brain or Brain()
    rag = rag or StubRag()
    await register_kargoshop_tools(tenant_id, SHOP_URL)
    async with tenant_session(tenant_id) as session:
        conversation = m.Conversation(tenant_id=tenant_id, customer_id=customer_id)
        session.add(conversation)
        await session.flush()
        conversation_id = conversation.id

    store = Store()
    runner = ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(store))))
    llm = FakeLLM(brain.chat, brain.tools)
    guard = None
    if firewall:
        config = firewall if isinstance(firewall, FirewallConfig) else FirewallConfig()
        guard = Firewall(LlmInjectionClassifier(llm), config)
    service = SupervisorService(
        llm, rag, runner, checkpointer, memory, guard, ToolGuard(runner) if tool_guard else None
    )
    return Env(tenant_id, conversation_id, store, llm, brain, rag, service, customer_id, anonymous)
