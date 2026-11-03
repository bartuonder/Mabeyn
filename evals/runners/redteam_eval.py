import argparse
import asyncio
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from httpx import ASGITransport
from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apps.kargoshop_mock.main import Store, create_app
from evals.budget import budget_limit, configure_ledger, ensure_within_budget, spent_usd
from evals.cache import CachingLLM
from evals.datasets import DATASETS_DIR
from evals.provenance import provenance
from evals.redteam_scoring import (
    Attack,
    BenignMessage,
    BenignOutcome,
    Observation,
    ObservedCall,
    attack_outcome,
    load_attacks,
    load_benign,
    load_public,
    summarize_attacks,
    summarize_benign,
    summarize_detection,
)
from evals.runner import CHUNK_PRESETS, drop_tenant, prepare_tenant
from evals.tracking import RESULTS_DIR, log_wandb, save_result
from mabeyn.agents.checkpoint import open_checkpointer, setup_checkpoint_tables
from mabeyn.agents.service import SupervisorService, TurnContext
from mabeyn.core.config import get_settings
from mabeyn.core.loop import use_selector_loop_on_windows
from mabeyn.core.tracking import export_tracking_env
from mabeyn.db import models as m
from mabeyn.db.provisioning import create_tenant
from mabeyn.db.session import tenant_session
from mabeyn.guard.basic import check_input
from mabeyn.guard.canary import canary_for
from mabeyn.guard.classifier import LlmInjectionClassifier
from mabeyn.guard.firewall import Firewall
from mabeyn.guard.policies import ToolGuard
from mabeyn.ingestion.pipeline import add_document, ingest_document
from mabeyn.llm import usage
from mabeyn.llm.client import ChatMessage, get_llm
from mabeyn.memory.schema import MemoryRecord
from mabeyn.memory.service import MemoryService
from mabeyn.memory.store import MemoryStore, open_memory_store, setup_store_tables
from mabeyn.rag.graph import GraphRag, RagConfig
from mabeyn.rag.service import Final
from mabeyn.seed import register_kargoshop_tools
from mabeyn.tools.executor import HttpToolExecutor
from mabeyn.tools.service import ToolRunner

SHOP_URL = "http://shop"
MOCK_TODAY = date(2026, 11, 1)
OTHER_CUSTOMER_NOTES = {
    "customer-2": [
        MemoryRecord(category="preference", slug="carrier", text="Prefers Aras Kargo."),
        MemoryRecord(category="context", slug="region", text="Lives in KKTC."),
    ]
}


@dataclass
class Arena:
    mode: str
    tenant_id: uuid.UUID
    other_tenant_id: uuid.UUID
    firewall: Firewall | None
    llm: CachingLLM
    rag: GraphRag
    saver: Any
    store: MemoryStore
    memory: MemoryService
    poisoned_titles: list[str]


def load_docs() -> list[dict[str, Any]]:
    path = DATASETS_DIR / "redteam_docs.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def ingest_text(
    tenant_id: uuid.UUID, title: str, text: str, llm: CachingLLM, firewall: Firewall | None
) -> None:
    async with tenant_session(tenant_id) as session:
        document = await add_document(
            session,
            tenant_id=tenant_id,
            title=title,
            source_type=m.DocumentSource.MARKDOWN,
            data=text.encode(),
            language="en",
        )
        document_id = document.id
    await ingest_document(tenant_id, document_id, llm=llm, firewall=firewall)


async def build_arena(
    mode: str, engine: AsyncEngine, llm: CachingLLM, saver: Any, store: MemoryStore
) -> Arena:
    on = mode == "on"
    firewall = Firewall(LlmInjectionClassifier(llm)) if on else None
    settings = get_settings()
    tenant_id = await prepare_tenant(
        engine,
        llm,
        chunk_config=CHUNK_PRESETS["default"],
        embedding_model=settings.embedding_model,
        label=f"redteam-{mode}",
        firewall=firewall,
    )
    await register_kargoshop_tools(tenant_id, SHOP_URL)
    poisoned = []
    other_tenant_id = tenant_id
    for doc in load_docs():
        if doc.get("tenant") == "other":
            other_tenant_id = await create_tenant(
                engine,
                "Zeta Retail",
                f"zeta-{uuid.uuid4().hex[:8]}",
                f"z-{uuid.uuid4().hex[:8]}@example.com",
                "eval-password-1",
            )
            await ingest_text(other_tenant_id, doc["title"], doc["text"], llm, None)
        else:
            await ingest_text(tenant_id, doc["title"], doc["text"], llm, firewall)
            poisoned.append(doc["title"])
    for customer, records in OTHER_CUSTOMER_NOTES.items():
        await store.save(tenant_id, customer, records)
    return Arena(
        mode,
        tenant_id,
        other_tenant_id,
        firewall,
        llm,
        GraphRag(llm, RagConfig(canary_guard=on)),
        saver,
        store,
        MemoryService(llm, store),
        poisoned,
    )


async def reset(arena: Arena) -> None:
    async with tenant_session(arena.tenant_id) as session:
        await session.execute(delete(m.Approval))
        await session.execute(delete(m.ToolCall))


async def new_conversation(arena: Arena, customer_id: str) -> uuid.UUID:
    async with tenant_session(arena.tenant_id) as session:
        conversation = m.Conversation(tenant_id=arena.tenant_id, customer_id=customer_id)
        session.add(conversation)
        await session.flush()
        return conversation.id


async def chat_turns(
    arena: Arena, customer_id: str, messages: list[str]
) -> tuple[list[Final], uuid.UUID, Store]:
    shop = Store()
    runner = ToolRunner(HttpToolExecutor(transport=ASGITransport(app=create_app(shop))))
    on = arena.firewall is not None
    service = SupervisorService(
        arena.llm,
        arena.rag,
        runner,
        arena.saver,
        arena.store,
        arena.firewall,
        ToolGuard(runner) if on else None,
    )
    conversation_id = await new_conversation(arena, customer_id)
    turn = TurnContext(arena.tenant_id, customer_id, conversation_id, "KargoShop", today=MOCK_TODAY)
    history: list[ChatMessage] = []
    finals: list[Final] = []
    for text in messages:
        final = None
        async for event in service.run(turn, text, history):
            if isinstance(event, Final):
                final = event
        if final is None:
            raise RuntimeError("the agent produced no answer")
        finals.append(final)
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": final.answer})
    await arena.saver.adelete_thread(turn.thread_id)
    return finals, conversation_id, shop


async def poison_session(arena: Arena, attack: Attack) -> None:
    """Session 1 of a memory attack: messages go through the same input layer as real chat."""
    conversation_id = await new_conversation(arena, attack.customer_id)
    for text in attack.session1:
        blocked = False
        if arena.firewall is not None:
            decision = await arena.firewall.inspect_input(
                arena.tenant_id, conversation_id, text, check_input(text).text
            )
            blocked = not decision.allowed
        async with tenant_session(arena.tenant_id) as session:
            session.add(
                m.Message(
                    tenant_id=arena.tenant_id,
                    conversation_id=conversation_id,
                    role=m.MessageRole.USER,
                    content=text,
                    blocked=blocked,
                )
            )
    await arena.memory.extract(arena.tenant_id, conversation_id)


async def observed_calls(arena: Arena) -> list[ObservedCall]:
    async with tenant_session(arena.tenant_id) as session:
        rows = await session.scalars(select(m.ToolCall))
        return [ObservedCall(r.tool_name, r.arguments, r.status.value) for r in rows]


async def run_attack(arena: Arena, attack: Attack) -> dict[str, Any]:
    await reset(arena)
    await arena.memory.forget(arena.tenant_id, attack.customer_id)
    with usage.measure() as meter:
        if attack.session1:
            await poison_session(arena, attack)
        finals, _, shop = await chat_turns(
            arena, attack.customer_id, [attack.message, *attack.turns]
        )
        notes = [n.text for n in await arena.store.list(arena.tenant_id, attack.customer_id)]
        observation = Observation(
            answers=[f.answer for f in finals],
            calls=await observed_calls(arena),
            notes=notes,
            refunds_executed=len(shop.refunds),
            canary=canary_for(arena.tenant_id),
        )
    await arena.memory.forget(arena.tenant_id, attack.customer_id)
    reasons = attack_outcome(attack, observation)
    return {
        "id": attack.id,
        "category": attack.category,
        "mode": arena.mode,
        "succeeded": bool(reasons),
        "reasons": reasons,
        "statuses": [f.status for f in finals],
        "answers": [a[:300] for a in observation.answers],
        "notes": notes,
        "cost_usd": meter.cost_usd,
    }


async def run_benign(arena: Arena, item: BenignMessage) -> dict[str, Any]:
    await reset(arena)
    with usage.measure() as meter:
        finals, conversation_id, shop = await chat_turns(arena, item.customer_id, [item.message])
    async with tenant_session(arena.tenant_id) as session:
        events = list(
            await session.scalars(
                select(m.SecurityEvent).where(m.SecurityEvent.conversation_id == conversation_id)
            )
        )
    blocked = [f"{e.layer}:{e.category}" for e in events if e.action is m.SecurityAction.BLOCKED]
    flagged = [f"{e.layer}:{e.category}" for e in events if e.action is m.SecurityAction.FLAGGED]
    outcome = BenignOutcome(
        item.id, finals[0].status, blocked, flagged, len(shop.refunds), item.expect
    )
    return {
        **asdict(outcome),
        "false_block": outcome.false_block,
        "message": item.message,
        "answer": finals[0].answer[:300],
        "cost_usd": meter.cost_usd,
    }


async def run_public(arena: Arena, firewall: Firewall) -> dict[str, Any]:
    rows = load_public()
    results = []
    for row in rows:
        ensure_within_budget()
        clean = check_input(row["text"]).text
        decision = await firewall.inspect_input(arena.tenant_id, None, row["text"], clean)
        results.append({"id": row["id"], "label": row["label"], "blocked": not decision.allowed})
    metrics = summarize_detection([r["label"] for r in results], [r["blocked"] for r in results])
    return {"metrics": metrics, "rows": results}


async def seed_corpus_flags(arena: Arena) -> dict[str, float]:
    """How often the document layer flags innocent seed passages and the poisoned ones."""
    async with tenant_session(arena.tenant_id) as session:
        rows = (
            await session.execute(
                select(
                    m.Document.title,
                    func.count(m.Chunk.id),
                    func.sum(case((m.Chunk.flagged, 1), else_=0)),
                )
                .join(m.Chunk, m.Chunk.document_id == m.Document.id)
                .group_by(m.Document.title)
            )
        ).all()
    clean = [(t, n, int(f or 0)) for t, n, f in rows if t not in arena.poisoned_titles]
    poisoned = [(t, n, int(f or 0)) for t, n, f in rows if t in arena.poisoned_titles]
    return {
        "seed_chunks": float(sum(n for _, n, _ in clean)),
        "seed_chunks_flagged": float(sum(f for _, _, f in clean)),
        "poisoned_documents": float(len(poisoned)),
        "poisoned_documents_flagged": float(sum(1 for _, _, f in poisoned if f > 0)),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    use_selector_loop_on_windows()
    export_tracking_env()
    configure_ledger()
    os.environ["TOOL_ALLOWED_HOSTS"] = "shop"
    get_settings.cache_clear()
    settings = get_settings()

    attacks = load_attacks()
    benign = load_benign()
    if args.categories:
        attacks = [a for a in attacks if a.category in args.categories]
    if args.limit:
        attacks, benign = attacks[: args.limit], benign[: args.limit]

    llm = CachingLLM(get_llm())
    engine = create_async_engine(settings.admin_database_url)
    await setup_checkpoint_tables()
    await setup_store_tables()
    attack_results: list[dict[str, Any]] = []
    benign_results: list[dict[str, Any]] = []
    public: dict[str, Any] = {}
    corpus: dict[str, float] = {}
    created: list[uuid.UUID] = []
    try:
        async with open_checkpointer() as saver, open_memory_store() as store:
            for mode in args.modes:
                arena = await build_arena(mode, engine, llm, saver, store)
                created += [arena.tenant_id, arena.other_tenant_id]
                for attack in attacks:
                    ensure_within_budget()
                    result = await run_attack(arena, attack)
                    attack_results.append(result)
                    mark = "HIT " if result["succeeded"] else "held"
                    print(
                        f"{mode:<3} {mark} {attack.id} {attack.category:<16} {attack.message[:50]}"
                    )
                if mode == "on":
                    corpus = await seed_corpus_flags(arena)
                    for item in benign if not args.skip_benign else []:
                        ensure_within_budget()
                        result = await run_benign(arena, item)
                        benign_results.append(result)
                        mark = "BLOCK" if result["false_block"] else "ok   "
                        print(f"on  {mark} {item.id} {item.message[:60]}")
                    if not args.skip_public and arena.firewall is not None:
                        public = await run_public(arena, arena.firewall)
    finally:
        for tenant in created:
            await drop_tenant(engine, tenant)
        await engine.dispose()

    categories = {a.id: a.category for a in attacks}
    metrics: dict[str, float] = {}
    for mode in args.modes:
        outcomes = {r["id"]: r["succeeded"] for r in attack_results if r["mode"] == mode}
        for name, value in summarize_attacks(outcomes, categories).items():
            metrics[f"{mode}_{name}"] = value
    if benign_results:
        outs = [
            BenignOutcome(
                r["id"],
                r["status"],
                r["blocked_events"],
                r["flagged_events"],
                r["refunds_executed"],
                r["expect"],
            )
            for r in benign_results
        ]
        metrics.update(summarize_benign(outs))
        metrics["benign_cost_usd"] = sum(r["cost_usd"] for r in benign_results)
    metrics.update(public.get("metrics", {}))
    metrics.update(corpus)
    metrics["attack_cost_usd"] = sum(r["cost_usd"] for r in attack_results)

    config = {
        "modes": args.modes,
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
        "attacks": attack_results,
        "benign": benign_results,
        "public": public.get("rows", []),
    }
    path = save_result(args.name, result)
    log_wandb(args.name, config, metrics, tags=["redteam"])
    lines = [
        "| Metric | Value |",
        "| --- | ---: |",
        *[f"| {k} | {v:.3f} |" for k, v in metrics.items()],
    ]
    (RESULTS_DIR / f"{args.name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\ncache hits/misses {llm.hits}/{llm.misses}")
    print(f"spend ${spent_usd():.3f} of ${budget_limit():.2f}")
    print(f"saved {path}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="redteam_eval")
    parser.add_argument("--name", default="redteam-eval")
    parser.add_argument("--modes", nargs="+", default=["off", "on"], choices=["off", "on"])
    parser.add_argument("--categories", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--skip-benign", action="store_true")
    parser.add_argument("--skip-public", action="store_true")
    args = parser.parse_args()
    use_selector_loop_on_windows()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
