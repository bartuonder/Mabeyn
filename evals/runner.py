import asyncio
import math
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from evals.budget import ensure_within_budget
from evals.datasets import Question
from evals.judge import judge_accuracy, judge_faithfulness
from evals.metrics import (
    chunk_matches,
    mean,
    percentile,
    recall_at_k,
    reciprocal_rank,
    wilson_interval,
)
from mabeyn.db.models import Tenant
from mabeyn.db.provisioning import create_tenant
from mabeyn.ingestion.chunk import ChunkConfig
from mabeyn.llm import usage
from mabeyn.llm.client import LLM
from mabeyn.rag.service import Final, RagService, collect
from mabeyn.seed import ingest_seed_documents, load_seed_documents

COMPANY = "KargoShop"
RECALL_KS = (1, 3, 5, 8)

CHUNK_PRESETS = {
    "default": ChunkConfig(300, 600),
    "fine": ChunkConfig(80, 160),
    "medium": ChunkConfig(150, 300),
    "coarse": ChunkConfig(500, 900),
}


@dataclass
class QuestionResult:
    id: str
    category: str
    answerable: bool
    question: str
    answer: str
    abstained: bool
    ranked: list[dict[str, str]]
    gold_hit: bool
    recall: dict[str, float]
    mrr: float
    correct: bool
    judge_reason: str
    faithful: bool
    unsupported: list[str]
    latency_s: float
    cost_usd: float
    llm_calls: int


async def prepare_tenant(
    engine: AsyncEngine,
    llm: LLM,
    *,
    chunk_config: ChunkConfig,
    embedding_model: str | None,
    label: str,
) -> uuid.UUID:
    """Create a throwaway tenant holding the full demo corpus under the given settings."""
    suffix = uuid.uuid4().hex[:8]
    tenant_id = await create_tenant(
        engine,
        COMPANY,
        f"eval-{label}-{suffix}"[:60],
        f"eval-{suffix}@example.com",
        "eval-password-1",
    )
    await ingest_seed_documents(
        tenant_id, load_seed_documents(), llm, chunk_config, embedding_model
    )
    return tenant_id


async def drop_tenant(engine: AsyncEngine, tenant_id: uuid.UUID) -> None:
    async with AsyncSession(engine) as session, session.begin():
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))


def _label(chunk: Any) -> dict[str, str]:
    return {"doc": chunk.source_uri or "", "section": chunk.section, "title": chunk.document_title}


async def run_question(
    service: RagService, tenant_id: uuid.UUID, question: Question, judge_llm: LLM
) -> QuestionResult:
    ensure_within_budget()
    with usage.measure() as meter:
        started = time.perf_counter()
        final: Final = await collect(service.run(tenant_id, question.question, [], COMPANY))
        wall = time.perf_counter() - started
    latency = max(wall + meter.latency_adjustment_s, 0.0)

    ranked = final.ranked or final.chunks
    recalls = {f"recall@{k}": recall_at_k(ranked, question.gold, k) for k in RECALL_KS}
    accuracy = await judge_accuracy(
        judge_llm, question.question, question.expected_answer, final.answer
    )
    faithfulness = await judge_faithfulness(judge_llm, final.answer, final.chunks)
    return QuestionResult(
        id=question.id,
        category=question.category,
        answerable=question.answerable,
        question=question.question,
        answer=final.answer,
        abstained=final.abstained,
        ranked=[_label(c) for c in ranked],
        gold_hit=any(chunk_matches(c, g) for c in ranked[:5] for g in question.gold),
        recall=recalls,
        mrr=reciprocal_rank(ranked, question.gold),
        correct=accuracy.correct,
        judge_reason=accuracy.reason,
        faithful=faithfulness.faithful,
        unsupported=list(faithfulness.unsupported),
        latency_s=latency,
        cost_usd=meter.cost_usd,
        llm_calls=meter.calls,
    )


async def run_retrieval_only(
    service: RagService, tenant_id: uuid.UUID, question: Question
) -> QuestionResult:
    """Cheap variant for retrieval experiments: no judge calls."""
    ensure_within_budget()
    with usage.measure() as meter:
        started = time.perf_counter()
        final = await collect(service.run(tenant_id, question.question, [], COMPANY))
        wall = time.perf_counter() - started
    ranked = final.ranked or final.chunks
    return QuestionResult(
        id=question.id,
        category=question.category,
        answerable=question.answerable,
        question=question.question,
        answer=final.answer,
        abstained=final.abstained,
        ranked=[_label(c) for c in ranked],
        gold_hit=any(chunk_matches(c, g) for c in ranked[:5] for g in question.gold),
        recall={f"recall@{k}": recall_at_k(ranked, question.gold, k) for k in RECALL_KS},
        mrr=reciprocal_rank(ranked, question.gold),
        correct=False,
        judge_reason="",
        faithful=False,
        unsupported=[],
        latency_s=max(wall + meter.latency_adjustment_s, 0.0),
        cost_usd=meter.cost_usd,
        llm_calls=meter.calls,
    )


async def evaluate(
    service: RagService,
    tenant_id: uuid.UUID,
    questions: list[Question],
    judge_llm: LLM,
    *,
    concurrency: int = 1,
    retrieval_only: bool = False,
) -> list[QuestionResult]:
    gate = asyncio.Semaphore(concurrency)

    async def one(question: Question) -> QuestionResult:
        async with gate:
            if retrieval_only:
                return await run_retrieval_only(service, tenant_id, question)
            return await run_question(service, tenant_id, question, judge_llm)

    return list(await asyncio.gather(*(one(q) for q in questions)))


def _rate(items: list[QuestionResult], attr: str) -> float:
    return mean([float(getattr(r, attr)) for r in items]) if items else math.nan


def summarize(results: list[QuestionResult], retrieval_only: bool = False) -> dict[str, float]:
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]
    metrics: dict[str, float] = {
        "n": float(len(results)),
        "n_answerable": float(len(answerable)),
        "mrr": mean([r.mrr for r in answerable]),
    }
    for k in RECALL_KS:
        metrics[f"recall@{k}"] = mean([r.recall[f"recall@{k}"] for r in answerable])
    if not retrieval_only:
        metrics["accuracy"] = _rate(results, "correct")
        metrics["accuracy_answerable"] = _rate(answerable, "correct")
        metrics["accuracy_unanswerable"] = _rate(unanswerable, "correct")
        metrics["faithfulness"] = _rate(results, "faithful")
        for key, attr in (("accuracy", "correct"), ("faithfulness", "faithful")):
            low, high = wilson_interval(sum(getattr(r, attr) for r in results), len(results))
            metrics[f"{key}_ci95_low"], metrics[f"{key}_ci95_high"] = low, high
    metrics["latency_p50_s"] = percentile([r.latency_s for r in results], 0.50)
    metrics["latency_p95_s"] = percentile([r.latency_s for r in results], 0.95)
    metrics["cost_per_query_usd"] = mean([r.cost_usd for r in results])
    metrics["llm_calls_per_query"] = mean([float(r.llm_calls) for r in results])
    return metrics


def accuracy_by_category(results: list[QuestionResult]) -> dict[str, float]:
    categories = sorted({r.category for r in results})
    return {c: _rate([r for r in results if r.category == c], "correct") for c in categories}


def to_json(results: list[QuestionResult]) -> list[dict[str, Any]]:
    return [asdict(r) for r in results]
