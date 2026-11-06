"""Replay evaluation questions through the real pipeline and print every model call.

Used for error analysis: the result files say which questions failed, this shows why (which passages
were retrieved, which the grader kept, what the answer was, what the verifier said). It builds a
throwaway tenant with the demo corpus, so embeddings come from the evaluation cache when present and
each question costs a fraction of a cent.

    uv run python -m evals.runners.diagnose tr-080 xl-006
"""

import argparse
import asyncio
import re
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from evals.budget import configure_ledger
from evals.cache import CachingLLM
from evals.configs import build_service
from evals.datasets import load_questions
from evals.runner import CHUNK_PRESETS, COMPANY, drop_tenant, prepare_tenant
from mabeyn.core.config import get_settings
from mabeyn.llm.client import get_llm
from mabeyn.rag.service import collect

DATASETS = (
    "rag.jsonl",
    "rag_en.jsonl",
    "rag_xlang.jsonl",
    "rag_heldout.jsonl",
    "rag_heldout_v2.jsonl",
)
PASSAGE = re.compile(r"\[(\d+)\] ([^\n(]{0,90})")


class TracingLLM:
    """Prints a short summary of every chat call and passes everything else through."""

    def __init__(self, inner: CachingLLM) -> None:
        self.inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def chat(self, messages: list[Any], **kwargs: Any) -> Any:
        result = await self.inner.chat(messages, **kwargs)
        system = messages[0]["content"][:70].replace("\n", " ")
        user = messages[-1]["content"]
        print(f"\n--- [{system}] model={kwargs.get('model')}")
        passages = PASSAGE.findall(user)[:8]
        if passages:
            print("passages:", [(n, title.strip()) for n, title in passages])
        if "Answer:" in user:
            print("answer checked:", user.split("Answer:")[-1][:400].replace("\n", " "))
        print("output:", result.text[:500].replace("\n", " "))
        return result


async def run(ids: list[str]) -> None:
    configure_ledger()
    settings = get_settings()
    base = CachingLLM(get_llm())
    questions = {q.id: q for name in DATASETS for q in load_questions(name)}
    unknown = [i for i in ids if i not in questions]
    if unknown:
        raise SystemExit(f"unknown question ids: {unknown}")

    engine = create_async_engine(settings.admin_database_url)
    tenant_id = await prepare_tenant(
        engine,
        base,
        chunk_config=CHUNK_PRESETS["default"],
        embedding_model=settings.embedding_model,
        label="diagnose",
    )
    try:
        service = build_service("full", TracingLLM(base), settings.embedding_model)  # type: ignore[arg-type]
        for question_id in ids:
            question = questions[question_id]
            print(f"\n========== {question_id}: {question.question}")
            print("expected:", question.expected_answer)
            final = await collect(service.run(tenant_id, question.question, [], COMPANY))
            print(f"\nfinal (abstained={final.abstained}):", final.answer[:500])
    finally:
        await drop_tenant(engine, tenant_id)
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="diagnose")
    parser.add_argument("ids", nargs="+", help="question ids such as tr-080 or xl-006")
    asyncio.run(run(parser.parse_args().ids))


if __name__ == "__main__":
    main()
