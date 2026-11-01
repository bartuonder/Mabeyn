import argparse
import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from evals.budget import budget_limit, configure_ledger, spent_usd
from evals.cache import CachingLLM
from evals.configs import ABLATION_LABELS, ABLATION_ORDER, CONFIGS, build_service
from evals.datasets import load_questions
from evals.reporting import build_result, markdown_table
from evals.runner import CHUNK_PRESETS, drop_tenant, evaluate, prepare_tenant, summarize
from evals.tracking import RESULTS_DIR, log_wandb, save_result
from mabeyn.core.config import get_settings
from mabeyn.core.tracking import export_tracking_env
from mabeyn.llm.client import get_llm

COLUMNS = [
    ("Recall@5", "recall@5", 3),
    ("MRR", "mrr", 3),
    ("Accuracy", "accuracy", 3),
    ("Faithfulness", "faithfulness", 3),
    ("p95 latency (s)", "latency_p95_s", 2),
    ("Cost / query ($)", "cost_per_query_usd", 5),
]


async def run(args: argparse.Namespace) -> None:
    export_tracking_env()
    configure_ledger()
    settings = get_settings()
    llm = CachingLLM(get_llm())
    engine = create_async_engine(settings.admin_database_url)
    embedding_model = args.embedding or settings.embedding_model
    questions = load_questions(args.dataset)
    if args.limit:
        questions = questions[: args.limit]

    tenant_id = await prepare_tenant(
        engine,
        llm,
        chunk_config=CHUNK_PRESETS[args.chunk],
        embedding_model=embedding_model,
        label="ablation",
    )
    rows: list[tuple[str, dict[str, float]]] = []
    try:
        for name in args.configs:
            service = build_service(name, llm, embedding_model)
            results = await evaluate(
                service, tenant_id, questions, llm, concurrency=args.concurrency
            )
            config: dict[str, Any] = {
                "config": name,
                "dataset": args.dataset,
                "chunk_preset": args.chunk,
                "embedding_model": embedding_model,
                "llm_model": settings.llm_model,
                "llm_model_small": settings.llm_model_small,
                "judge_model": settings.judge_model,
            }
            result = build_result(f"{args.prefix}-{name}", config, results)
            save_result(f"{args.prefix}-{name}", result)
            log_wandb(f"{args.prefix}-{name}", config, result["metrics"], ["ablation", name])
            rows.append((ABLATION_LABELS.get(name, name), summarize(results)))
            print(
                f"{name}: accuracy={rows[-1][1]['accuracy']:.3f} "
                f"recall@5={rows[-1][1]['recall@5']:.3f} spend=${spent_usd():.3f}"
            )
    finally:
        await drop_tenant(engine, tenant_id)
        await engine.dispose()

    table = markdown_table(rows, COLUMNS)
    (RESULTS_DIR / f"{args.prefix}.md").write_text(table + "\n", encoding="utf-8")
    print("\n" + table)
    print(f"\ntotal spend ${spent_usd():.3f} of ${budget_limit():.2f}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ablation")
    p.add_argument("--prefix", default="ablation")
    p.add_argument("--configs", nargs="*", default=ABLATION_ORDER, choices=sorted(CONFIGS))
    p.add_argument("--dataset", default="rag.jsonl")
    p.add_argument("--limit", type=int)
    p.add_argument("--chunk", default="default", choices=sorted(CHUNK_PRESETS))
    p.add_argument("--embedding")
    p.add_argument("--concurrency", type=int, default=1)
    return p


def main() -> None:
    asyncio.run(run(parser().parse_args()))


if __name__ == "__main__":
    main()
