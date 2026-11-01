import json

from evals.judge import JUDGE_OUTCOME_SYSTEM, judge_outcome
from tests.fakes import FakeLLM


async def test_the_outcome_judge_reads_both_verdicts():
    llm = FakeLLM(
        lambda m, model: json.dumps({"outcome_ok": True, "language_ok": False, "reason": "x"})
    )
    verdict = await judge_outcome(llm, "Where is KS-1?", "Says it shipped.", "Kargoda.")
    assert verdict.outcome_ok and not verdict.language_ok
    system, user = llm.chat_calls[0][1]
    assert system["content"] == JUDGE_OUTCOME_SYSTEM
    assert "Expected outcome: Says it shipped." in user["content"]


async def test_an_unreadable_judge_reply_counts_as_a_failure():
    verdict = await judge_outcome(FakeLLM(lambda m, model: "nonsense"), "q", "o", "a")
    assert not verdict.outcome_ok and not verdict.language_ok
