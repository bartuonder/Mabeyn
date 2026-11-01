import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Protocol

from mabeyn.llm.client import LLM, ChatMessage, get_llm
from mabeyn.rag.generate import extract_citations, safe_fallback, stream_answer
from mabeyn.rag.retrieval import retrieve_vector
from mabeyn.rag.types import Citation, RetrievedChunk


@dataclass(frozen=True)
class Token:
    text: str


@dataclass(frozen=True)
class Retract:
    """The text streamed so far must be discarded; `Final.answer` replaces it."""


@dataclass(frozen=True)
class Final:
    answer: str
    citations: list[Citation]
    chunks: list[RetrievedChunk]
    abstained: bool = False
    trace: dict[str, object] = field(default_factory=dict)


RagEvent = Token | Retract | Final


class RagService(Protocol):
    def run(
        self,
        tenant_id: uuid.UUID,
        question: str,
        history: Sequence[ChatMessage],
        company: str,
    ) -> AsyncIterator[RagEvent]: ...


class BaselineRag:
    """Vector search followed by one cited generation call."""

    def __init__(self, llm: LLM, top_k: int = 5) -> None:
        self.llm = llm
        self.top_k = top_k

    async def run(
        self,
        tenant_id: uuid.UUID,
        question: str,
        history: Sequence[ChatMessage],
        company: str,
    ) -> AsyncIterator[RagEvent]:
        chunks = await retrieve_vector(tenant_id, self.llm, question, self.top_k)
        pieces: list[str] = []
        async for piece in stream_answer(self.llm, company, question, chunks, history):
            pieces.append(piece)
            yield Token(piece)
        answer = "".join(pieces).strip()
        if not answer:
            answer = await safe_fallback(self.llm, question)
        yield Final(answer=answer, citations=extract_citations(answer, chunks), chunks=chunks)


async def collect(events: AsyncIterator[RagEvent]) -> Final:
    final: Final | None = None
    async for event in events:
        if isinstance(event, Final):
            final = event
    if final is None:
        raise RuntimeError("RAG service finished without a final event")
    return final


@lru_cache
def get_rag_service() -> RagService:
    return BaselineRag(get_llm())
