from collections.abc import Callable
from typing import Any

from mabeyn.llm.client import LLM
from mabeyn.rag.graph import GraphRag, RagConfig
from mabeyn.rag.service import BaselineRag, RagService

ServiceFactory = Callable[[LLM, str | None], RagService]


def graph(**overrides: Any) -> ServiceFactory:
    def factory(llm: LLM, embedding_model: str | None) -> RagService:
        return GraphRag(llm, RagConfig(embedding_model=embedding_model, **overrides))

    return factory


CONFIGS: dict[str, ServiceFactory] = {
    "vector": lambda llm, embedding_model: BaselineRag(llm, 5, embedding_model),
    "hybrid": graph(name="hybrid", rerank=False, crag=False, faithfulness=False, final_k=5),
    "vector-rerank": graph(
        name="vector-rerank", retrieval="vector", crag=False, faithfulness=False
    ),
    "hybrid-rerank": graph(name="hybrid-rerank", crag=False, faithfulness=False),
    "hybrid-rerank-crag": graph(name="hybrid-rerank-crag", faithfulness=False),
    "full": graph(name="full"),
}

# Each row adds one technique to the row above it.
ABLATION_ORDER = ["vector", "hybrid", "hybrid-rerank", "hybrid-rerank-crag", "full"]
ABLATION_LABELS = {
    "vector": "Vector only",
    "hybrid": "+ Hybrid (FTS + RRF)",
    "hybrid-rerank": "+ Reranker",
    "hybrid-rerank-crag": "+ CRAG grading",
    "full": "+ Faithfulness check",
    "vector-rerank": "Vector + reranker",
}


def build_service(name: str, llm: LLM, embedding_model: str | None = None) -> RagService:
    try:
        return CONFIGS[name](llm, embedding_model)
    except KeyError:
        raise SystemExit(f"unknown config {name!r}; available: {', '.join(CONFIGS)}") from None
