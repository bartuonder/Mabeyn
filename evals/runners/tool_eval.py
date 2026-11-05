import argparse
import asyncio
import os
import time
import uuid
from dataclasses import asdict
from datetime import date
from typing import Any

from httpx import ASGITransport
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine

from apps.kargoshop_mock.main import Store, create_app
from evals.budget import budget_limit, configure_ledger, ensure_within_budget, spent_usd
from evals.cache import CachingLLM
from evals.judge import judge_outcome
from evals.metrics import wilson_interval
from evals.provenance import provenance
from evals.runner import CHUNK_PRESETS, drop_tenant, prepare_tenant
from evals.tool_scoring import (
    ObservedCall,
    Scenario,
    ScenarioScore,
    load_scenarios,
    score_scenario,
    summarize_scores,
)
from evals.tracking import RESULTS_DIR, log_wandb, save_result
from mabeyn.agents.checkpoint import open_checkpointer, setup_checkpoint_tables
from mabeyn.agents.service import SupervisorService, TurnContext
from mabeyn.core.config import get_settings
from mabeyn.core.loop import use_selector_loop_on_windows
from mabeyn.core.tracking import export_tracking_env
from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.guard.policies import ToolGuard
from mabeyn.llm import usage
from mabeyn.llm.client import ChatMessage, get_llm
from mabeyn.rag.graph import GraphRag, RagConfig
from mabeyn.rag.service import Final
from mabeyn.seed import register_kargoshop_tools
from mabeyn.tools.executor import HttpToolExecutor
from mabeyn.tools.service import ToolRunner

SHOP_URL = "http://shop"
MOCK_TODAY = date(2026, 11, 1)  # matches the date in seed/kargoshop/orders.json


async def _approval_counts(tenant_id: uuid.UUID, conversation_id: uuid.UUID) -> tuple[int, int]:
    """Sensitive tool calls of a conversation and how many of them have an approval request."""
    async with tenant_session(tenant_id) as session:
        sensitive = set(
            await session.scalars(
                select(m.ToolDefinition.name).where(
                    m.ToolDefinition.risk_level == m.RiskLevel.SENSITIVE
                )
            )
        )
        calls = list(
            await session.scalars(
                select(m.ToolCall).where(m.ToolCall.conversation_id == conversation_id)
            )
        )
        approved = set(
            await session.scalars(
                select(m.Approval.tool_call_id).where(m.Approval.conversation_id == conversation_id)
            )
        )
    risky = [c for c in calls if c.tool_name in sensitive]
    return len(risky), sum(1 for c in risky if c.id in approved)


async def run_scenario(
    scenario: Scenario, tenant_id: uuid.UUID, llm: CachingLLM, rag: GraphRag, saver: Any
) -> dict[str, Any]:
    async with tenant_session(tenant_id) as session:
        conversation = m.Conversation(tenant_id=tenant_id, customer_id=scenario.customer_id)
        session.add(conversation)
        await session.flush()
        conversation_id = conversation.id

    async with tenant_session(tenant_id) as session:
        # The write rate limit counts earlier scenarios of the same customer otherwise.
        await session.execute(delete(m.Approval))
        await session.execute(delete(m.ToolCall))
    store = Store()
    runner = ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(store))))
    service = SupervisorService(llm, rag, runner, saver, tool_guard=ToolGuard(runner))
    turn = TurnContext(
        tenant_id, scenario.customer_id, conversation_id, "KargoShop", today=MOCK_TODAY
    )

    history: list[ChatMessage] = [
        {"role": t["role"], "content": t["content"]} for t in scenario.history
    ]
    with usage.measure() as meter:
        started = time.perf_counter()
        final: Final | None = None
        async for event in service.run(turn, scenario.message, history):
            if isinstance(event, Final):
                final = event
        wall = time.perf_counter() - started
    if final is None:
        raise RuntimeError(f"{scenario.id}: the agent produced no answer")

    trace: list[dict[str, Any]] = list(final.trace.get("tool_trace", []))  # type: ignore[call-overload]
    # A call the policies stopped never reached the shop or an approval, so the defence held.
    calls = [
        ObservedCall(t["name"], t.get("arguments", {}), t["status"])
        for t in trace
        if t["status"] != "blocked"
    ]
    policy_blocks = sum(1 for t in trace if t["status"] == "blocked")
    sensitive, with_approval = await _approval_counts(tenant_id, conversation_id)
    verdict = await judge_outcome(llm, scenario.message, scenario.outcome, final.answer)
    score = score_scenario(
        scenario,
        calls,
        final.trace.get("route", []),  # type: ignore[arg-type]
        final.status,
        final.answer,
        sensitive,
        with_approval,
        verdict,
    )
    await saver.adelete_thread(turn.thread_id)
    return {
        "score": score,
        "answer": final.answer,
        "status": final.status,
        "route": final.trace.get("route", []),
        "calls": [asdict(c) for c in calls],
        "refunds_executed": len(store.refunds),
        "policy_blocks": policy_blocks,
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

    scenarios = load_scenarios(args.dataset)
    if args.categories:
        scenarios = [s for s in scenarios if s.category in args.categories]
    if args.ids:
        scenarios = [s for s in scenarios if s.id in set(args.ids)]
    if args.limit:
        scenarios = scenarios[: args.limit]

    llm = CachingLLM(get_llm())
    engine = create_async_engine(settings.admin_database_url)
    tenant_id = await prepare_tenant(
        engine,
        llm,
        chunk_config=CHUNK_PRESETS["default"],
        embedding_model=settings.embedding_model,
        label="tools",
    )
    await register_kargoshop_tools(tenant_id, SHOP_URL)
    await setup_checkpoint_tables()

    outcomes: list[dict[str, Any]] = []
    try:
        async with open_checkpointer() as saver:
            rag = GraphRag(llm, RagConfig())
            for scenario in scenarios:
                ensure_within_budget()
                outcome = await run_scenario(scenario, tenant_id, llm, rag, saver)
                outcome["id"], outcome["message"] = scenario.id, scenario.message
                outcomes.append(outcome)
                mark = "ok  " if outcome["score"].passed else "FAIL"
                print(f"{mark} {scenario.id} {scenario.category:<14} {scenario.message[:60]}")
    finally:
        await drop_tenant(engine, tenant_id)
        await engine.dispose()

    scores: list[ScenarioScore] = [o["score"] for o in outcomes]
    metrics = summarize_scores(scores)
    executed = sum(o["refunds_executed"] for o in outcomes if o["status"] == "pending_approval")
    metrics["sensitive_executed_without_approval"] = float(executed)
    metrics["policy_blocks"] = float(sum(o["policy_blocks"] for o in outcomes))
    metrics["cost_per_scenario_usd"] = sum(o["cost_usd"] for o in outcomes) / max(len(outcomes), 1)
    metrics["latency_p50_s"] = sorted(o["latency_s"] for o in outcomes)[len(outcomes) // 2]
    low, high = wilson_interval(sum(s.passed for s in scores), len(scores))
    metrics["scenario_pass_rate_ci95_low"], metrics["scenario_pass_rate_ci95_high"] = low, high

    config = {
        "dataset": args.dataset,
        "llm_model": settings.llm_model,
        "llm_model_small": settings.llm_model_small,
        "categories": args.categories,
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
    log_wandb(args.name, config, metrics, tags=["tool-eval"])
    failures = [o for o in outcomes if not o["score"].passed]

    lines = ["| Metric | Value |", "| --- | ---: |"]
    lines += [f"| {k} | {v:.3f} |" for k, v in metrics.items()]
    (RESULTS_DIR / f"{args.name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    for o in failures:
        score: ScenarioScore = o["score"]
        print(f"\n{o['id']} [{score.category}] {o['message']}")
        print(f"  calls={o['calls']} status={o['status']} route={o['route']} notes={score.notes}")
    print(f"\ncache hits/misses {llm.hits}/{llm.misses}")
    print(f"spend ${spent_usd():.3f} of ${budget_limit():.2f}")
    print(f"saved {path}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="tool_eval")
    parser.add_argument("--name", default="tool-eval")
    parser.add_argument("--dataset", default="tools.jsonl")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--categories", nargs="*")
    parser.add_argument("--ids", nargs="*")
    args = parser.parse_args()
    use_selector_loop_on_windows()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
