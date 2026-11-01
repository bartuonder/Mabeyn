import json

import httpx
import pytest
from openai import AsyncOpenAI

from mabeyn.llm import usage
from mabeyn.llm.client import LLMError, OpenAILLM, chat_json, parse_json


def make_llm(handler) -> OpenAILLM:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAILLM(AsyncOpenAI(api_key="test", http_client=http, max_retries=0))


def chat_response(text: str) -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4.1-mini",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
    }


async def test_chat_returns_text_and_records_usage():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=chat_response("hello"))

    with usage.measure() as meter:
        result = await make_llm(handler).chat(
            [{"role": "user", "content": "hi"}], model="gpt-4.1-nano", json_mode=True
        )
    assert result.text == "hello"
    assert (result.input_tokens, result.output_tokens) == (12, 3)
    assert seen["model"] == "gpt-4.1-nano"
    assert seen["response_format"] == {"type": "json_object"}
    assert meter.calls == 1 and meter.input_tokens == 12


async def test_stream_yields_deltas_and_records_usage():
    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "gpt-4.1-mini"}
    body = (
        sse({**base, "choices": [{"index": 0, "delta": {"content": "Hel"}, "finish_reason": None}]})
        + sse(
            {**base, "choices": [{"index": 0, "delta": {"content": "lo"}, "finish_reason": None}]}
        )
        + sse(
            {
                **base,
                "choices": [],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
            }
        )
        + "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    with usage.measure() as meter:
        pieces = [p async for p in make_llm(handler).stream([{"role": "user", "content": "hi"}])]
    assert "".join(pieces) == "Hello"
    assert meter.input_tokens == 7 and meter.output_tokens == 2


async def test_embed_batches_and_keeps_order():
    batches = []

    def handler(request: httpx.Request) -> httpx.Response:
        inputs = json.loads(request.content)["input"]
        batches.append(len(inputs))
        data = [
            {"object": "embedding", "index": i, "embedding": [float(i)]}
            for i in reversed(range(len(inputs)))
        ]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": data,
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            },
        )

    vectors = await make_llm(handler).embed([f"t{i}" for i in range(150)])
    assert batches == [100, 50]
    assert vectors[0] == [0.0] and vectors[99] == [99.0] and vectors[100] == [0.0]
    assert len(vectors) == 150


def test_parse_json_strips_code_fences():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_rejects_garbage():
    with pytest.raises(LLMError):
        parse_json("not json")


async def test_chat_json_parses_model_reply():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=chat_response('{"ok": true}'))

    assert await chat_json(make_llm(handler), [{"role": "user", "content": "x"}]) == {"ok": True}


async def test_chat_with_tools_returns_tool_calls_and_sends_the_tools():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        body = chat_response("")
        body["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "call_9",
                "type": "function",
                "function": {"name": "get_order_status", "arguments": '{"order_id": "KS-1"}'},
            }
        ]
        body["choices"][0]["finish_reason"] = "tool_calls"
        return httpx.Response(200, json=body)

    tools = [{"type": "function", "function": {"name": "get_order_status", "parameters": {}}}]
    result = await make_llm(handler).chat_with_tools([{"role": "user", "content": "where?"}], tools)

    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [
        ("call_9", "get_order_status", '{"order_id": "KS-1"}')
    ]
    assert seen["tools"] == tools and seen["parallel_tool_calls"] is False
    echoed = result.as_assistant_message()
    assert echoed["tool_calls"][0]["function"]["name"] == "get_order_status"


async def test_chat_with_tools_plain_answer_has_no_calls():
    llm = make_llm(lambda request: httpx.Response(200, json=chat_response("hello")))
    result = await llm.chat_with_tools([{"role": "user", "content": "hi"}], [])
    assert result.text == "hello" and result.tool_calls == ()
    assert "tool_calls" not in result.as_assistant_message()
