import argparse
import asyncio
import os
import time
import uuid
from dataclasses import asdict
from datetime import date
from typing import Any

from httpx import ASGITransport
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from apps.kargoshop_mock.main import Store, create_app
from evals.budget import budget_limit, configure_ledger, ensure_within_budget, spent_usd
from evals.cache import CachingLLM
from evals.judge import judge_outcome
from evals.memory_scoring import (
    MemoryScenario,
    MemoryScore,
    StoredNote,
    load_memory_scenarios,
    score_memory_scenario,
    summarize_memory_scores,
)
from evals.provenance import provenance
from evals.runner import CHUNK_PRESETS, drop_tenant, prepare_tenant
from evals.tracking import RESULTS_DIR, log_wandb, save_result
from mabeyn.agents.checkpoint import open_checkpointer, setup_checkpoint_tables
from mabeyn.agents.service import SupervisorService, TurnContext
from mabeyn.core.config import get_settings
from mabeyn.core.loop import use_selector_loop_on_windows
from mabeyn.core.tracking import export_tracking_env
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.llm import usage
from mabeyn.llm.client import get_llm
from mabeyn.memory.service import MemoryService
from mabeyn.memory.store import MemoryStore, open_memory_store, setup_store_tables
from mabeyn.rag.graph import GraphRag, RagConfig
from mabeyn.rag.service import Final
from mabeyn.seed import register_kargoshop_tools
from mabeyn.tools.executor import HttpToolExecutor
from mabeyn.tools.service import ToolRunner

SHOP_URL = "http://shop"
MOCK_TODAY = date(2026, 11, 1)


async def _first_session(
    tenant_id: uuid.UUID, scenario: MemoryScenario, memory: MemoryService
) -> int:
    """The customer's first conversation: only their messages matter for extraction."""
    async with tenant_session(tenant_id) as session:
        conversation = m.Conversation(tenant_id=tenant_id, customer_id=scenario.customer_id)
        session.add(conversation)
        await session.flush()
        conversation_id = conversation.id
    for text in scenario.session1:
        # One transaction per message, like real chat turns, so the order is well defined.
        async with tenant_session(tenant_id) as session:
            session.add(
                m.Message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role=m.MessageRole.USER,
                    content=text,
                )
            )
    await memory.extract(tenant_id, conversation_id)
    async with tenant_session(tenant_id) as session:
        blocked = await session.scalar(
            select(func.count())
            .select_from(m.SecurityEvent)
            .where(m.SecurityEvent.conversation_id == conversation_id)
        )
    return int(blocked or 0)


async def run_scenario(
    scenario: MemoryScenario,
    tenant_id: uuid.UUID,
    llm: CachingLLM,
    rag: GraphRag,
    saver: Any,
    store: MemoryStore,
    judge_model: str | None,
) -> dict[str, Any]:
    memory = MemoryService(llm, store)
    customers = {scenario.customer_id, scenario.session2_customer_id}
    for customer in customers:
        await memory.forget(tenant_id, customer)

    with usage.measure() as meter:
        started = time.perf_counter()
        blocked = await _first_session(tenant_id, scenario, memory)
        notes = [
            StoredNote(n.category.value, n.text)
            for n in await store.list(tenant_id, scenario.customer_id)
        ]

        async with tenant_session(tenant_id) as session:
            conversation = m.Conversation(
                tenant_id=tenant_id, customer_id=scenario.session2_customer_id
            )
            session.add(conversation)
            await session.flush()
            conversation_id = conversation.id
        shop = Store()
        runner = ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(shop))))
        service = SupervisorService(llm, rag, runner, saver, store)
        turn = TurnContext(
            tenant_id, scenario.session2_customer_id, conversation_id, "KargoShop", today=MOCK_TODAY
        )
        final: Final | None = None
        async for event in service.run(turn, scenario.message, []):
            if isinstance(event, Final):
                final = event
        wall = time.perf_counter() - started
    if final is None:
        raise RuntimeError(f"{scenario.id}: the agent produced no answer")

    verdict = (
        await judge_outcome(llm, scenario.message, scenario.outcome, final.answer, judge_model)
        if scenario.outcome
        else None
    )
    score = score_memory_scenario(
        scenario, notes, final.answer, final.status, len(shop.refunds), blocked, verdict
    )
    await saver.adelete_thread(turn.thread_id)
    for customer in customers:
        await memory.forget(tenant_id, customer)
    return {
        "score": score,
        "notes": [asdict(n) for n in notes],
        "answer": final.answer,
        "status": final.status,
        "route": final.trace.get("route", []),
        "latency_s": max(wall + meter.latency_adjustment_s, 0.0),
        "cost_usd": meter.cost_usd,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    use_selector_loop_on_windows()
    export_tracking_env()
    configure_ledger()
    os.environ["TOOL_ALLOWED_HOSTS"] = "shop"
    get_settings.cache_clear()
    settings = get_settings()

    scenarios = load_memory_scenarios(args.dataset)
    if args.kinds:
        scenarios = [s for s in scenarios if s.kind in args.kinds]
    if args.limit:
        scenarios = scenarios[: args.limit]

    llm = CachingLLM(get_llm())
    engine = create_async_engine(settings.admin_database_url)
    tenant_id = await prepare_tenant(
        engine,
        llm,
        chunk_config=CHUNK_PRESETS["default"],
        embedding_model=settings.embedding_model,
        label="memory",
    )
    await register_kargoshop_tools(tenant_id, SHOP_URL)
    await setup_checkpoint_tables()
    await setup_store_tables()

    outcomes: list[dict[str, Any]] = []
    try:
        async with open_checkpointer() as saver, open_memory_store() as store:
            rag = GraphRag(llm, RagConfig())
            for scenario in scenarios:
                ensure_within_budget()
                outcome = await run_scenario(
                    scenario, tenant_id, llm, rag, saver, store, args.judge_model
                )
                outcome["id"], outcome["kind"] = scenario.id, scenario.kind
                outcome["message"] = scenario.message
                outcomes.append(outcome)
                mark = "ok  " if outcome["score"].passed else "FAIL"
                print(f"{mark} {scenario.id} {scenario.kind:<14} {scenario.message[:56]}")
    finally:
        await drop_tenant(engine, tenant_id)
        await engine.dispose()

    scores: list[MemoryScore] = [o["score"] for o in outcomes]
    metrics = summarize_memory_scores(scores)
    metrics["cost_per_scenario_usd"] = sum(o["cost_usd"] for o in outcomes) / max(len(outcomes), 1)
    metrics["latency_p50_s"] = sorted(o["latency_s"] for o in outcomes)[len(outcomes) // 2]

    config = {
        "dataset": args.dataset,
        "llm_model": settings.llm_model,
        "llm_model_small": settings.llm_model_small,
        "judge_model": args.judge_model or settings.judge_model,
        "kinds": args.kinds,
    }
    result = {
        "name": args.name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provenance": provenance(),
        "config": config,
        "metrics": metrics,
        "scenarios": [
            {
                **{k: v for k, v in o.items() if k != "score"},
                **asdict(o["score"]),
                "passed": o["score"].passed,
            }
            for o in outcomes
        ],
    }
    path = save_result(args.name, result)
    log_wandb(args.name, config, metrics, tags=["memory-eval"])

    lines = ["| Metric | Value |", "| --- | ---: |"]
    lines += [f"| {k} | {v:.3f} |" for k, v in metrics.items()]
    (RESULTS_DIR / f"{args.name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    for o in outcomes:
        if not o["score"].passed:
            print(f"\n{o['id']} [{o['kind']}] {o['message']}")
            print(f"  notes={o['notes']}\n  answer={o['answer'][:300]}")
            print(f"  status={o['status']} route={o['route']} score={asdict(o['score'])}")
    print(f"\ncache hits/misses {llm.hits}/{llm.misses}")
    print(f"spend ${spent_usd():.3f} of ${budget_limit():.2f}")
    print(f"saved {path}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="memory_eval")
    parser.add_argument("--name", default="memory-eval")
    parser.add_argument("--dataset", default="memory.jsonl")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--kinds", nargs="*")
    parser.add_argument("--judge-model", default=None)
    args = parser.parse_args()
    use_selector_loop_on_windows()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
