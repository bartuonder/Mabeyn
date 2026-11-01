import hashlib
import math
import re
from collections.abc import AsyncIterator, Callable, Sequence

from mabeyn.core.config import get_settings
from mabeyn.llm.client import ChatMessage, ChatResult

ChatHandler = Callable[[Sequence[ChatMessage], str], str]


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

    def __init__(self, handler: ChatHandler | None = None) -> None:
        self.handler = handler or (lambda messages, model: "{}")
        self.chat_calls: list[tuple[str, list[ChatMessage]]] = []
        self.embed_calls: list[list[str]] = []

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
        for piece in re.findall(r"\S+\s*", text):
            yield piece

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [hash_embed(t) for t in texts]
