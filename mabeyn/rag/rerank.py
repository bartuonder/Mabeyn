from collections.abc import Sequence
from typing import Protocol

from mabeyn.core.config import get_settings
from mabeyn.llm.client import LLM, LLMError, chat_json
from mabeyn.rag import prompts
from mabeyn.rag.types import RetrievedChunk

PASSAGE_CHARS = 700


class Reranker(Protocol):
    async def rerank(
        self, query: str, chunks: Sequence[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]: ...


class LLMReranker:
    """Listwise reranking with a small model: one call orders all candidates."""

    def __init__(self, llm: LLM, model: str | None = None) -> None:
        self.llm = llm
        self.model = model

    async def rerank(
        self, query: str, chunks: Sequence[RetrievedChunk], top_n: int
    ) -> list[RetrievedChunk]:
        candidates = list(chunks)
        if len(candidates) <= 1:
            return candidates[:top_n]

        passages = "\n\n".join(
            f"[{i}] {c.context_header}\n{c.content[:PASSAGE_CHARS]}"
            for i, c in enumerate(candidates, start=1)
        )
        try:
            data = await chat_json(
                self.llm,
                [
                    {"role": "system", "content": prompts.RERANK_SYSTEM},
                    {"role": "user", "content": f"Question: {query}\n\nPassages:\n{passages}"},
                ],
                model=self.model or get_settings().llm_model_small,
                max_tokens=200,
            )
            ranking = data["ranking"]
        except (LLMError, KeyError, TypeError):
            return candidates[:top_n]

        order: list[int] = []
        for value in ranking if isinstance(ranking, list) else []:
            if isinstance(value, int) and 1 <= value <= len(candidates) and value not in order:
                order.append(value)
        order.extend(i for i in range(1, len(candidates) + 1) if i not in order)
        return [candidates[i - 1] for i in order][:top_n]
