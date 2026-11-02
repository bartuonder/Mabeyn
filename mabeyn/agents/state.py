from typing import Any, Literal, TypedDict

from mabeyn.llm.client import ChatMessage

Status = Literal["answered", "pending_approval", "escalated", "blocked"]


class SupervisorState(TypedDict, total=False):
    """Graph state. Everything is JSON-safe so it can be checkpointed.

    Retrieved document chunks are never stored here: the action agent only ever sees the
    knowledge agent's answer text, never the raw documents.
    """

    # Identity comes from the verified session, never from the model.
    tenant_id: str
    customer_id: str
    conversation_id: str
    company: str
    today: str
    language: str

    question: str
    history: list[ChatMessage]
    summary: str
    memories: list[dict[str, str]]

    blocked: bool
    plan: list[str]
    step: int
    route_reason: str
    knowledge_question: str
    streamed: bool

    knowledge_answer: str
    knowledge_citations: list[dict[str, Any]]
    knowledge_abstained: bool

    action_messages: list[ChatMessage]
    action_answer: str
    tool_calls_used: int
    tool_errors: int
    tool_trace: list[dict[str, Any]]
    pending: dict[str, Any] | None
    pending_message: str

    needs_human: bool
    escalation_reason: str

    answer: str
    status: Status
    approval_id: str | None


def fresh_turn() -> SupervisorState:
    """Values that reset at the start of every customer message."""
    return {
        "blocked": False,
        "memories": [],
        "plan": [],
        "step": 0,
        "route_reason": "",
        "knowledge_question": "",
        "language": "",
        "streamed": False,
        "knowledge_answer": "",
        "knowledge_citations": [],
        "knowledge_abstained": False,
        "action_messages": [],
        "action_answer": "",
        "tool_calls_used": 0,
        "tool_errors": 0,
        "tool_trace": [],
        "pending": None,
        "pending_message": "",
        "needs_human": False,
        "escalation_reason": "",
        "answer": "",
        "status": "answered",
        "approval_id": None,
    }
