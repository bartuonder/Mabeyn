import json
import math

from evals.budget import BudgetExceededError, ensure_within_budget
from evals.datasets import load_questions
from evals.runner import CHUNK_PRESETS, evaluate, summarize
from evals.tracking import save_result
from mabeyn.rag import prompts
from mabeyn.rag.service import BaselineRag
from mabeyn.seed import ingest_seed_documents, load_seed_documents
from tests.fakes import FakeLLM


def judge_handler(messages, model) -> str:
    system = messages[0]["content"]
    if system == prompts.JUDGE_ACCURACY_SYSTEM:
        return json.dumps({"correct": True, "reason": "ok"})
    return json.dumps({"faithful": True, "unsupported": []})


async def test_evaluation_produces_metrics(fresh_tenant):
    llm = FakeLLM(lambda m, model: "Thirty days [Iade Politikasi:Iade Suresi].")
    await ingest_seed_documents(fresh_tenant, load_seed_documents(), llm, CHUNK_PRESETS["fine"])
    questions = load_questions("rag.jsonl", sets=("v0",))[:6]
    judge = FakeLLM(judge_handler)

    results = await evaluate(BaselineRag(llm, 5), fresh_tenant, questions, judge, concurrency=2)
    metrics = summarize(results)

    assert len(results) == 6
    assert metrics["n"] == 6.0
    assert 0.0 <= metrics["recall@5"] <= 1.0
    assert metrics["recall@1"] <= metrics["recall@3"] <= metrics["recall@5"]
    assert metrics["accuracy"] == 1.0 and metrics["faithfulness"] == 1.0
    assert metrics["llm_calls_per_query"] >= 2.0
    assert all(r.ranked for r in results)


async def test_retrieval_only_mode_skips_the_judge(fresh_tenant):
    llm = FakeLLM(lambda m, model: "answer")
    await ingest_seed_documents(fresh_tenant, load_seed_documents(), llm, CHUNK_PRESETS["fine"])
    questions = load_questions("rag.jsonl", sets=("v0",))[:4]
    judge = FakeLLM(judge_handler)

    results = await evaluate(
        BaselineRag(llm, 5), fresh_tenant, questions, judge, retrieval_only=True
    )
    metrics = summarize(results, retrieval_only=True)
    assert judge.chat_calls == []
    assert "accuracy" not in metrics and "mrr" in metrics


def test_unanswerable_questions_do_not_affect_recall():
    from evals.runner import QuestionResult

    def result(answerable: bool, recall: float) -> QuestionResult:
        return QuestionResult(
            id="x", category="c", answerable=answerable, question="q", answer="a",
            abstained=False, ranked=[], gold_hit=False,
            recall={"recall@1": recall, "recall@3": recall, "recall@5": recall},
            mrr=recall, correct=True, judge_reason="", faithful=True, unsupported=[],
            latency_s=1.0, cost_usd=0.0, llm_calls=1,
        )  # fmt: skip

    metrics = summarize([result(True, 1.0), result(False, math.nan)])
    assert metrics["recall@5"] == 1.0 and metrics["n_answerable"] == 1.0


def test_results_are_saved_as_strict_json(tmp_path):
    path = save_result("unit", {"metrics": {"x": math.nan, "y": 1.5}}, tmp_path)
    assert json.loads(path.read_text())["metrics"] == {"x": None, "y": 1.5}


def test_budget_guard_blocks_when_limit_is_reached(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"cost_usd": 3.0}) + "\n")
    monkeypatch.setattr("evals.budget.LEDGER_PATH", ledger)
    monkeypatch.setenv("EVAL_BUDGET_USD", "5")
    ensure_within_budget()
    monkeypatch.setenv("EVAL_BUDGET_USD", "2.5")
    try:
        ensure_within_budget()
        raise AssertionError("expected the budget guard to trip")
    except BudgetExceededError as exc:
        assert "evaluation budget" in str(exc)
