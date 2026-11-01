import argparse
import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from evals.budget import budget_limit, configure_ledger, spent_usd
from evals.cache import CachingLLM
from evals.configs import CONFIGS, build_service
from evals.datasets import load_questions
from evals.reporting import build_result
from evals.runner import CHUNK_PRESETS, drop_tenant, evaluate, prepare_tenant
from evals.tracking import log_wandb, save_result
from mabeyn.core.config import get_settings
from mabeyn.core.tracking import export_tracking_env
from mabeyn.llm.client import get_llm


def format_metrics(metrics: dict[str, float]) -> str:
    return "\n".join(f"  {key:<24}{value:.4f}" for key, value in metrics.items())


async def run(args: argparse.Namespace) -> dict[str, Any]:
    export_tracking_env()
    configure_ledger()
    settings = get_settings()
    llm = CachingLLM(get_llm())
    engine = create_async_engine(settings.admin_database_url)
    embedding_model = args.embedding or settings.embedding_model

    questions = load_questions(args.dataset, tuple(args.sets) if args.sets else None)
    if args.limit:
        questions = questions[: args.limit]

    tenant_id = await prepare_tenant(
        engine,
        llm,
        chunk_config=CHUNK_PRESETS[args.chunk],
        embedding_model=embedding_model,
        label=args.name,
    )
    try:
        service = build_service(args.config, llm, embedding_model)
        results = await evaluate(
            service,
            tenant_id,
            questions,
            llm,
            concurrency=args.concurrency,
            retrieval_only=args.retrieval_only,
        )
    finally:
        if not args.keep_tenant:
            await drop_tenant(engine, tenant_id)
        await engine.dispose()

    config = {
        "config": args.config,
        "dataset": args.dataset,
        "sets": args.sets,
        "chunk_preset": args.chunk,
        "embedding_model": embedding_model,
        "llm_model": settings.llm_model,
        "llm_model_small": settings.llm_model_small,
        "judge_model": settings.judge_model,
        "retrieval_only": args.retrieval_only,
    }
    result = build_result(args.name, config, results, retrieval_only=args.retrieval_only)
    metrics = result["metrics"]
    path = save_result(args.name, result)
    logged = log_wandb(args.name, config, metrics, tags=[args.config, args.chunk])

    print(f"\n{args.name}: {len(results)} questions")
    print(format_metrics(metrics))
    print(f"  cache hits/misses       {llm.hits}/{llm.misses}")
    print(f"  wandb logged            {logged}")
    print(f"  total spend (ledger)    ${spent_usd():.3f} of ${budget_limit():.2f}")
    print(f"  saved                   {path}")
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rag_eval")
    p.add_argument("--name", required=True)
    p.add_argument("--config", default="vector", choices=sorted(CONFIGS))
    p.add_argument("--dataset", default="rag.jsonl")
    p.add_argument("--sets", nargs="*", help="dataset subsets, e.g. v0 v1")
    p.add_argument("--limit", type=int)
    p.add_argument("--chunk", default="default", choices=sorted(CHUNK_PRESETS))
    p.add_argument("--embedding")
    p.add_argument("--concurrency", type=int, default=1)
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument("--keep-tenant", action="store_true")
    return p


def main() -> None:
    asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
