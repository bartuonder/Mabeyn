from functools import lru_cache

from mabeyn.llm.client import get_llm
from mabeyn.rag.graph import GraphRag, RagConfig
from mabeyn.rag.service import RagService


@lru_cache
def get_rag_service() -> RagService:
    return GraphRag(get_llm(), RagConfig())
