import argparse
import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from evals.budget import budget_limit, configure_ledger, spent_usd
from evals.cache import CachingLLM
from evals.datasets import load_questions
from evals.reporting import build_result, markdown_table
from evals.runner import CHUNK_PRESETS, drop_tenant, evaluate, prepare_tenant, summarize
from evals.tracking import RESULTS_DIR, log_wandb, save_result
from mabeyn.core.config import get_settings
from mabeyn.core.tracking import export_tracking_env
from mabeyn.llm.client import LLM, get_llm
from mabeyn.rag.graph import GraphRag, RagConfig
from mabeyn.rag.service import BaselineRag, RagService

EMBEDDING_MODELS = ["text-embedding-3-small", "text-embedding-3-large"]
COLUMNS = [
    ("Recall@1", "recall@1", 3),
    ("Recall@3", "recall@3", 3),
    ("Recall@5", "recall@5", 3),
    ("Recall@8", "recall@8", 3),
    ("MRR", "mrr", 3),
]


@dataclass(frozen=True)
class Experiment:
    group: str
    label: str
    chunk: str
    embedding: str
    retrieval: str
    rerank: bool = False
    top_k: int = 8


def plan(chunk_presets: list[str], best_chunk: str | None) -> list[Experiment]:
    """Retrieval-only experiments; later groups reuse the best chunk preset when given."""
    items = [
        Experiment("chunk-size", f"{c} chunks, hybrid", c, EMBEDDING_MODELS[0], "hybrid")
        for c in chunk_presets
    ]
    chunk = best_chunk or "default"
    for model in EMBEDDING_MODELS:
        items.append(Experiment("embedding", f"{model}, vector", chunk, model, "vector"))
        items.append(Experiment("embedding", f"{model}, hybrid", chunk, model, "hybrid"))
    items.append(
        Experiment("reranker", "hybrid, reranker off", chunk, EMBEDDING_MODELS[0], "hybrid")
    )
    items.append(
        Experiment("reranker", "hybrid, reranker on", chunk, EMBEDDING_MODELS[0], "hybrid", True)
    )
    return items


def service_for(item: Experiment, llm: LLM) -> RagService:
    if item.retrieval == "vector" and not item.rerank:
        return BaselineRag(llm, item.top_k, item.embedding)
    config = RagConfig(
        name=item.label,
        retrieval="hybrid" if item.retrieval == "hybrid" else "vector",
        rerank=item.rerank,
        crag=False,
        faithfulness=False,
        final_k=item.top_k,
        embedding_model=item.embedding,
    )
    return GraphRag(llm, config)


async def run(args: argparse.Namespace) -> None:
    export_tracking_env()
    configure_ledger()
    llm = CachingLLM(get_llm())
    engine = create_async_engine(get_settings().admin_database_url)
    questions = load_questions(args.dataset)
    tenants: dict[tuple[str, str], Any] = {}
    sections: dict[str, list[tuple[str, dict[str, float]]]] = {}
    try:
        for item in plan(args.chunks, args.best_chunk):
            if args.groups and item.group not in args.groups:
                continue
            key = (item.chunk, item.embedding)
            if key not in tenants:
                tenants[key] = await prepare_tenant(
                    engine,
                    llm,
                    chunk_config=CHUNK_PRESETS[item.chunk],
                    embedding_model=item.embedding,
                    label=f"{item.chunk}-{item.embedding[-5:]}",
                )
            results = await evaluate(
                service_for(item, llm),
                tenants[key],
                questions,
                llm,
                concurrency=args.concurrency,
                retrieval_only=True,
            )
            metrics = summarize(results, retrieval_only=True)
            config = {
                "group": item.group,
                "label": item.label,
                "chunk_preset": item.chunk,
                "embedding_model": item.embedding,
                "retrieval": item.retrieval,
                "rerank": item.rerank,
                "top_k": item.top_k,
            }
            name = f"exp-{item.group}-{item.label}".replace(" ", "-").replace(",", "")
            save_result(name, build_result(name, config, results, retrieval_only=True))
            log_wandb(name, config, metrics, ["experiment", item.group])
            sections.setdefault(item.group, []).append((item.label, metrics))
            print(
                f"{item.group:<11}{item.label:<42}recall@5={metrics['recall@5']:.3f} "
                f"mrr={metrics['mrr']:.3f} spend=${spent_usd():.3f}"
            )
    finally:
        for tenant_id in tenants.values():
            await drop_tenant(engine, tenant_id)
        await engine.dispose()

    blocks = [f"### {group}\n\n{markdown_table(rows, COLUMNS)}" for group, rows in sections.items()]
    (RESULTS_DIR / args.output).write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    print("\n\n".join(blocks))
    print(f"\ntotal spend ${spent_usd():.3f} of ${budget_limit():.2f}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="experiments")
    p.add_argument("--dataset", default="rag.jsonl")
    p.add_argument("--chunks", nargs="*", default=["fine", "medium", "default", "coarse"])
    p.add_argument("--groups", nargs="*", choices=["chunk-size", "embedding", "reranker"])
    p.add_argument("--best-chunk", help="chunk preset for the embedding and reranker groups")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--output", default="experiments.md")
    return p


def main() -> None:
    asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
