import json
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, NotRequired, Protocol, TypedDict, cast

from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionStreamOptionsParam,
    ChatCompletionToolUnionParam,
)

from mabeyn.core.config import get_settings
from mabeyn.core.tracking import langsmith_enabled
from mabeyn.llm import usage

EMBED_BATCH_SIZE = 100


class ChatMessage(TypedDict):
    role: str
    content: str
    tool_calls: NotRequired[list[dict[str, Any]]]
    tool_call_id: NotRequired[str]


@dataclass(frozen=True)
class ChatResult:
    text: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it


@dataclass(frozen=True)
class ToolChatResult:
    text: str
    tool_calls: tuple[ToolCallRequest, ...]
    input_tokens: int
    output_tokens: int

    def as_assistant_message(self) -> ChatMessage:
        message: ChatMessage = {"role": "assistant", "content": self.text}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in self.tool_calls
            ]
        return message


class LLMError(Exception):
    pass


class LLM(Protocol):
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResult: ...

    async def chat_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ToolChatResult: ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]: ...

    async def embed(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> list[list[float]]: ...


def _to_params(messages: Sequence[ChatMessage]) -> list[ChatCompletionMessageParam]:
    return cast("list[ChatCompletionMessageParam]", list(messages))


class OpenAILLM:
    def __init__(self, client: AsyncOpenAI) -> None:
        self._client = client
        self._settings = get_settings()

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        model = model or self._settings.llm_model
        kwargs: dict[str, Any] = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(
            model=model,
            messages=_to_params(messages),
            temperature=temperature,
            max_completion_tokens=max_tokens,
            **kwargs,
        )
        used = response.usage
        input_tokens = used.prompt_tokens if used else 0
        output_tokens = used.completion_tokens if used else 0
        usage.record(model, input_tokens, output_tokens)
        return ChatResult(response.choices[0].message.content or "", input_tokens, output_tokens)

    async def chat_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> ToolChatResult:
        model = model or self._settings.llm_model
        response = await self._client.chat.completions.create(
            model=model,
            messages=_to_params(messages),
            tools=cast("list[ChatCompletionToolUnionParam]", list(tools)),
            tool_choice="auto",
            parallel_tool_calls=False,
            temperature=temperature,
            max_completion_tokens=max_tokens,
        )
        used = response.usage
        input_tokens = used.prompt_tokens if used else 0
        output_tokens = used.completion_tokens if used else 0
        usage.record(model, input_tokens, output_tokens)
        message = response.choices[0].message
        calls = tuple(
            ToolCallRequest(call.id, call.function.name, call.function.arguments)
            for call in (message.tool_calls or [])
            if call.type == "function"
        )
        return ToolChatResult(message.content or "", calls, input_tokens, output_tokens)

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        model = model or self._settings.llm_model
        response = await self._client.chat.completions.create(
            model=model,
            messages=_to_params(messages),
            temperature=temperature,
            max_completion_tokens=max_tokens,
            stream=True,
            stream_options=ChatCompletionStreamOptionsParam(include_usage=True),
        )
        async for chunk in response:
            if chunk.usage:
                usage.record(model, chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        model = model or self._settings.embedding_model
        kwargs: dict[str, Any] = {}
        if model.startswith("text-embedding-3"):
            kwargs["dimensions"] = self._settings.embedding_dimensions
        vectors: list[list[float]] = []
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = [t if t.strip() else " " for t in texts[start : start + EMBED_BATCH_SIZE]]
            response = await self._client.embeddings.create(model=model, input=batch, **kwargs)
            usage.record(model, response.usage.prompt_tokens, 0)
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors


@lru_cache
def get_llm() -> LLM:
    settings = get_settings()
    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.llm_timeout_seconds,
        max_retries=3,
    )
    if langsmith_enabled():
        # Records each model call as a child span of the graph node that made it.
        from langsmith.wrappers import wrap_openai

        client = wrap_openai(client)
    return OpenAILLM(client)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse_json(text: str) -> Any:
    try:
        return json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError as exc:
        raise LLMError(f"model did not return valid JSON: {text[:200]!r}") from exc


async def chat_json(
    llm: LLM,
    messages: Sequence[ChatMessage],
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Any:
    result = await llm.chat(messages, model=model, max_tokens=max_tokens, json_mode=True)
    return parse_json(result.text)
