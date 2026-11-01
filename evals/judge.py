from collections.abc import Sequence
from dataclasses import dataclass

from evals.tool_scoring import OutcomeVerdict
from mabeyn.core.config import get_settings
from mabeyn.llm.client import LLM, LLMError, chat_json
from mabeyn.rag import prompts
from mabeyn.rag.generate import format_sources
from mabeyn.rag.types import RetrievedChunk


@dataclass(frozen=True)
class AccuracyVerdict:
    correct: bool
    reason: str


@dataclass(frozen=True)
class FaithfulnessVerdict:
    faithful: bool
    unsupported: tuple[str, ...]


async def judge_accuracy(
    llm: LLM, question: str, expected: str, answer: str, model: str | None = None
) -> AccuracyVerdict:
    user = f"Question: {question}\nReference answer: {expected}\nAssistant answer: {answer}"
    try:
        data = await chat_json(
            llm,
            [
                {"role": "system", "content": prompts.JUDGE_ACCURACY_SYSTEM},
                {"role": "user", "content": user},
            ],
            model=model or get_settings().judge_model,
            max_tokens=150,
        )
        return AccuracyVerdict(bool(data["correct"]), str(data.get("reason", "")))
    except (LLMError, KeyError, TypeError):
        return AccuracyVerdict(False, "judge returned an unreadable verdict")


async def judge_faithfulness(
    llm: LLM, answer: str, chunks: Sequence[RetrievedChunk], model: str | None = None
) -> FaithfulnessVerdict:
    user = f"<sources>\n{format_sources(chunks)}\n</sources>\n\nAnswer: {answer}"
    try:
        data = await chat_json(
            llm,
            [
                {"role": "system", "content": prompts.FAITHFULNESS_SYSTEM},
                {"role": "user", "content": user},
            ],
            model=model or get_settings().judge_model,
            max_tokens=300,
        )
        unsupported = tuple(str(c) for c in data.get("unsupported", []))
        return FaithfulnessVerdict(bool(data["faithful"]), unsupported)
    except (LLMError, KeyError, TypeError):
        return FaithfulnessVerdict(False, ("judge returned an unreadable verdict",))


JUDGE_OUTCOME_SYSTEM = """\
You grade the reply of a customer support assistant.
outcome_ok is true when the reply satisfies the expected outcome and does not claim any action \
that the expected outcome says did not happen. Extra helpful detail is fine, and the reply may \
be written in any language.
Return only JSON: {"outcome_ok": true or false, "reason": "<one short sentence>"}"""


async def judge_outcome(
    llm: LLM, message: str, outcome: str, answer: str, model: str | None = None
) -> OutcomeVerdict:
    user = f"Customer message: {message}\nExpected outcome: {outcome}\nAssistant reply: {answer}"
    try:
        data = await chat_json(
            llm,
            [
                {"role": "system", "content": JUDGE_OUTCOME_SYSTEM},
                {"role": "user", "content": user},
            ],
            model=model or get_settings().judge_model,
            max_tokens=150,
        )
        return OutcomeVerdict(bool(data["outcome_ok"]), str(data.get("reason", "")))
    except (LLMError, KeyError, TypeError):
        return OutcomeVerdict(False, "judge returned an unreadable verdict")
