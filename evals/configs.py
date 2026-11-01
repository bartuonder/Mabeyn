from collections.abc import Callable

from mabeyn.llm.client import LLM
from mabeyn.rag.service import BaselineRag, RagService

ServiceFactory = Callable[[LLM, str | None], RagService]

CONFIGS: dict[str, ServiceFactory] = {
    "vector": lambda llm, embedding_model: BaselineRag(llm, 5, embedding_model),
}


def build_service(name: str, llm: LLM, embedding_model: str | None = None) -> RagService:
    try:
        return CONFIGS[name](llm, embedding_model)
    except KeyError:
        raise SystemExit(f"unknown config {name!r}; available: {', '.join(CONFIGS)}") from None
