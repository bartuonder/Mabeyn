import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from mabeyn.core.config import get_settings
from mabeyn.db.session import tenant_session
from mabeyn.guard.canary import canary_for
from mabeyn.llm.client import LLM, ChatMessage, LLMError, chat_json
from mabeyn.rag import prompts
from mabeyn.rag.generate import (
    extract_citations,
    format_sources,
    safe_fallback,
    stream_answer,
)
from mabeyn.rag.hybrid import hybrid_search
from mabeyn.rag.rerank import LLMReranker, Reranker
from mabeyn.rag.retrieval import embed_query, vector_search
from mabeyn.rag.service import Final, RagEvent, Retract, Token
from mabeyn.rag.types import RetrievedChunk

GRADE_PASSAGE_CHARS = 2000
GRADE_MAX_TOKENS = 160
FAITHFULNESS_MAX_TOKENS = 300


@dataclass(frozen=True)
class RagConfig:
    name: str = "full"
    retrieval: Literal["vector", "hybrid"] = "hybrid"
    rerank: bool = True
    crag: bool = True
    faithfulness: bool = True
    final_k: int = 6
    pool: int = 30
    max_rewrites: int = 2
    max_regenerations: int = 1
    embedding_model: str | None = None
    generate: bool = True
    canary_guard: bool = False


class RagState(TypedDict, total=False):
    tenant_id: uuid.UUID
    question: str
    history: list[ChatMessage]
    company: str
    language: str | None
    query: str
    rewrites: int
    ranked: list[RetrievedChunk]
    relevant: list[RetrievedChunk]
    answer: str
    faithful: bool
    regenerations: int
    abstained: bool


def _history_text(history: Sequence[ChatMessage]) -> str:
    return "\n".join(f"{m['role']}: {m['content']}" for m in history[-6:])


def _passages(chunks: Sequence[RetrievedChunk], limit: int = GRADE_PASSAGE_CHARS) -> str:
    def heading(i: int, chunk: RetrievedChunk) -> str:
        date = f" (effective_date: {chunk.effective_date})" if chunk.effective_date else ""
        return f"[{i}] {chunk.context_header}{date}"

    return "\n\n".join(
        f"{heading(i, c)}\n{c.content[:limit]}" for i, c in enumerate(chunks, start=1)
    )


def build_rag_graph(llm: LLM, config: RagConfig, reranker: Reranker | None = None) -> Any:
    settings = get_settings()
    small = settings.llm_model_small
    reranker = reranker or LLMReranker(llm, small)

    async def rewrite_query(state: RagState) -> dict[str, Any]:
        question, history = state["question"], state.get("history", [])
        if "query" not in state:
            if not history:
                return {"query": question, "rewrites": 0}
            system, user = (
                prompts.REWRITE_SYSTEM,
                f"Conversation:\n{_history_text(history)}\n\nLatest message: {question}",
            )
            rewrites = 0
        else:
            system = prompts.REWRITE_RETRY_SYSTEM
            user = f"Customer question: {question}\nPrevious query: {state['query']}"
            rewrites = state.get("rewrites", 0) + 1
        try:
            data = await chat_json(
                llm,
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                model=small,
                max_tokens=120,
            )
            query = str(data["query"]).strip() or question
        except (LLMError, KeyError, TypeError):
            query = question
        return {"query": query, "rewrites": rewrites}

    async def retrieve(state: RagState) -> dict[str, Any]:
        tenant_id, query = state["tenant_id"], state["query"]
        vector = await embed_query(llm, query, config.embedding_model)
        take = config.pool if config.rerank else config.final_k
        async with tenant_session(tenant_id) as session:
            if config.retrieval == "hybrid":
                found = await hybrid_search(session, tenant_id, query, vector, take, config.pool)
            else:
                found = await vector_search(session, tenant_id, vector, take)
        if config.rerank:
            found = await reranker.rerank(query, found, config.final_k)
        return {"ranked": found[: config.final_k]}

    async def grade_documents(state: RagState) -> dict[str, Any]:
        ranked = state["ranked"]
        if not config.crag or not ranked:
            return {"relevant": ranked}
        try:
            data = await chat_json(
                llm,
                [
                    {"role": "system", "content": prompts.GRADE_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Question: {state['question']}\n\nPassages:\n{_passages(ranked)}"
                        ),
                    },
                ],
                model=small,
                max_tokens=GRADE_MAX_TOKENS,
            )
            picked = [n for n in data["relevant"] if isinstance(n, int) and 1 <= n <= len(ranked)]
        except (LLMError, KeyError, TypeError):
            return {"relevant": ranked}
        return {"relevant": [ranked[n - 1] for n in dict.fromkeys(picked)]}

    def after_grade(state: RagState) -> str:
        if state["relevant"]:
            return "generate"
        if state.get("rewrites", 0) < config.max_rewrites:
            return "rewrite_query"
        return "abstain"

    async def generate(state: RagState) -> dict[str, Any]:
        if not config.generate:
            return {"answer": "(generation disabled)"}
        writer = get_stream_writer()
        strict = state.get("regenerations", 0) > 0
        pieces: list[str] = []
        guard = ""
        if config.canary_guard:
            guard = prompts.GUARD_TEMPLATE.format(canary=canary_for(state["tenant_id"]))
        async for piece in stream_answer(
            llm,
            state["company"],
            state["question"],
            state["relevant"],
            state.get("history", []),
            strict=strict,
            language=state.get("language"),
            guard=guard,
        ):
            pieces.append(piece)
            writer({"type": "token", "text": piece})
        return {"answer": "".join(pieces).strip()}

    async def check_faithfulness(state: RagState) -> dict[str, Any]:
        if not config.faithfulness or not state.get("answer"):
            return {"faithful": bool(state.get("answer"))}
        sources = format_sources(state["relevant"])
        user = f"<sources>\n{sources}\n</sources>\n\nAnswer: {state['answer']}"
        try:
            data = await chat_json(
                llm,
                [
                    {"role": "system", "content": prompts.FAITHFULNESS_SYSTEM},
                    {"role": "user", "content": user},
                ],
                model=small,
                max_tokens=FAITHFULNESS_MAX_TOKENS,
            )
            return {"faithful": bool(data["faithful"])}
        except (LLMError, KeyError, TypeError):
            return {"faithful": True}

    def after_check(state: RagState) -> str:
        if state["faithful"]:
            return END
        if state.get("regenerations", 0) < config.max_regenerations:
            return "regenerate"
        return "abstain"

    async def regenerate(state: RagState) -> dict[str, Any]:
        get_stream_writer()({"type": "retract"})
        return {"regenerations": state.get("regenerations", 0) + 1}

    async def abstain(state: RagState) -> dict[str, Any]:
        get_stream_writer()({"type": "retract"})
        return {"answer": await safe_fallback(llm, state["question"]), "abstained": True}

    graph = StateGraph(RagState)
    graph.add_node("rewrite_query", rewrite_query)
    graph.add_node("retrieve", retrieve)
    graph.add_node("grade_documents", grade_documents)
    graph.add_node("generate", generate)
    graph.add_node("check_faithfulness", check_faithfulness)
    graph.add_node("regenerate", regenerate)
    graph.add_node("abstain", abstain)

    graph.add_edge(START, "rewrite_query")
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges(
        "grade_documents",
        after_grade,
        {"generate": "generate", "rewrite_query": "rewrite_query", "abstain": "abstain"},
    )
    graph.add_edge("generate", "check_faithfulness")
    graph.add_conditional_edges(
        "check_faithfulness",
        after_check,
        {END: END, "regenerate": "regenerate", "abstain": "abstain"},
    )
    graph.add_edge("regenerate", "generate")
    graph.add_edge("abstain", END)
    return graph.compile()


class GraphRag:
    """RagService backed by the LangGraph subgraph."""

    def __init__(self, llm: LLM, config: RagConfig | None = None) -> None:
        self.llm = llm
        self.config = config or RagConfig()
        self.graph = build_rag_graph(llm, self.config)

    async def run(
        self,
        tenant_id: uuid.UUID,
        question: str,
        history: Sequence[ChatMessage],
        company: str,
        language: str | None = None,
    ) -> AsyncIterator[RagEvent]:
        initial: RagState = {
            "tenant_id": tenant_id,
            "question": question,
            "history": list(history),
            "company": company,
            "language": language,
        }
        run_config = {
            "run_name": f"rag-{self.config.name}",
            "tags": ["mabeyn-rag", self.config.name],
            "metadata": {
                "tenant_id": str(tenant_id),
                "prompt_version": prompts.PROMPT_VERSION,
                "model": get_settings().llm_model,
                "rag_config": self.config.name,
            },
        }
        state: RagState = initial
        async for mode, payload in self.graph.astream(
            initial, run_config, stream_mode=["custom", "values"]
        ):
            if mode == "custom":
                yield Token(payload["text"]) if payload["type"] == "token" else Retract()
            else:
                state = payload

        answer = state.get("answer", "")
        used = state.get("relevant", []) if not state.get("abstained") else []
        yield Final(
            answer=answer,
            citations=extract_citations(answer, used),
            chunks=used,
            ranked=state.get("ranked", []),
            abstained=bool(state.get("abstained")),
            trace={
                "query": state.get("query", question),
                "rewrites": state.get("rewrites", 0),
                "regenerations": state.get("regenerations", 0),
                "relevant": len(state.get("relevant", [])),
                "config": self.config.name,
            },
        )
