import hashlib
import json
import math
import re
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

from mabeyn.core.config import get_settings
from mabeyn.llm import usage
from mabeyn.llm.client import ChatMessage, ChatResult, ToolCallRequest, ToolChatResult

ChatHandler = Callable[[Sequence[ChatMessage], str], str]
ToolHandler = Callable[[Sequence[ChatMessage], Sequence[dict[str, Any]], str], ToolChatResult]


def tool_call(name: str, call_id: str = "call_1", **arguments: Any) -> ToolChatResult:
    """A model turn that asks for one tool call."""
    request = ToolCallRequest(call_id, name, json.dumps(arguments))
    return ToolChatResult("", (request,), 10, 5)


def say(text: str) -> ToolChatResult:
    """A model turn that answers in plain text."""
    return ToolChatResult(text, (), 10, 5)


def hash_embed(text: str, dimensions: int | None = None) -> list[float]:
    """Deterministic bag-of-words embedding: texts sharing words get similar vectors."""
    dimensions = dimensions or get_settings().embedding_dimensions
    vector = [0.0] * dimensions
    for word in re.findall(r"\w+", text.lower()):
        index = int(hashlib.md5(word.encode()).hexdigest(), 16) % dimensions  # noqa: S324
        vector[index] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class FakeLLM:
    """Scripted LLM for tests. `handler(messages, model)` returns the reply text."""

    def __init__(
        self, handler: ChatHandler | None = None, tool_handler: ToolHandler | None = None
    ) -> None:
        self.handler = handler or (lambda messages, model: "{}")
        self.tool_handler = tool_handler
        self.chat_calls: list[tuple[str, list[ChatMessage]]] = []
        self.tool_turns: list[list[ChatMessage]] = []
        self.embed_calls: list[list[str]] = []

    async def chat_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ToolChatResult:
        model = model or get_settings().llm_model
        self.tool_turns.append(list(messages))
        usage.record(model, 10, 5)
        if self.tool_handler is None:
            return say("")
        return self.tool_handler(messages, tools, model)

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        model = model or get_settings().llm_model
        self.chat_calls.append((model, list(messages)))
        usage.record(model, 10, 5)
        return ChatResult(self.handler(messages, model), 10, 5)

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        model = model or get_settings().llm_model
        self.chat_calls.append((model, list(messages)))
        text = self.handler(messages, model)
        usage.record(model, 10, 5)
        for piece in re.findall(r"\S+\s*", text):
            yield piece

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        usage.record(model or get_settings().embedding_model, len(texts), 0)
        return [hash_embed(t) for t in texts]


class FakeQueue:
    def __init__(self, fail: bool = False) -> None:
        self.jobs: list[tuple[uuid.UUID, uuid.UUID]] = []
        self.fail = fail

    async def enqueue(self, tenant_id: uuid.UUID, document_id: uuid.UUID) -> None:
        if self.fail:
            raise ConnectionError("redis is down")
        self.jobs.append((tenant_id, document_id))

    async def close(self) -> None:
        return None
