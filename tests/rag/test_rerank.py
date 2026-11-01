import json
import uuid

from mabeyn.rag import prompts
from mabeyn.rag.rerank import LLMReranker
from mabeyn.rag.types import RetrievedChunk
from tests.fakes import FakeLLM


def chunk(label: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title=label,
        source_uri=label,
        section=label,
        page=None,
        content=f"content of {label}",
        context_header=label,
        effective_date=None,
        score=0.0,
    )


def reranker(reply: str) -> tuple[LLMReranker, FakeLLM]:
    llm = FakeLLM(lambda messages, model: reply)
    return LLMReranker(llm), llm


async def test_ranking_from_the_model_is_applied():
    items = [chunk(x) for x in "abcd"]
    rr, llm = reranker(json.dumps({"ranking": [3, 1]}))
    result = await rr.rerank("q", items, 3)
    assert [c.document_title for c in result] == ["c", "a", "b"]
    assert llm.chat_calls[0][0] == "gpt-4o-mini"
    assert llm.chat_calls[0][1][0]["content"] == prompts.RERANK_SYSTEM


async def test_invalid_and_duplicate_numbers_are_ignored():
    items = [chunk(x) for x in "abc"]
    rr, _ = reranker(json.dumps({"ranking": [2, 2, 9, 0, "x", 1]}))
    result = await rr.rerank("q", items, 3)
    assert [c.document_title for c in result] == ["b", "a", "c"]


async def test_unreadable_reply_keeps_the_original_order():
    items = [chunk(x) for x in "abc"]
    for reply in ("not json", json.dumps({"other": 1}), json.dumps({"ranking": "nope"})):
        rr, _ = reranker(reply)
        assert [c.document_title for c in await rr.rerank("q", items, 2)] == ["a", "b"]


async def test_single_candidate_skips_the_model():
    rr, llm = reranker("{}")
    assert len(await rr.rerank("q", [chunk("a")], 5)) == 1
    assert await rr.rerank("q", [], 5) == []
    assert llm.chat_calls == []


async def test_passages_are_truncated_in_the_prompt():
    big = chunk("big")
    big = RetrievedChunk(**{**big.__dict__, "content": "x" * 5000})
    rr, llm = reranker(json.dumps({"ranking": [1, 2]}))
    await rr.rerank("q", [big, chunk("small")], 2)
    prompt = llm.chat_calls[0][1][1]["content"]
    assert len(prompt) < 2000
