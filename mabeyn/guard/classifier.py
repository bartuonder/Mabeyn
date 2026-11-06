from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from mabeyn.core.config import get_settings
from mabeyn.guard import prompts
from mabeyn.llm.client import LLM, LLMError, chat_json

MAX_CHARS = 3000
HISTORY_MESSAGES = 4
HISTORY_CHARS = 300
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class Verdict:
    score: float
    category: str
    reason: str = ""


class InjectionClassifier(Protocol):
    async def classify(
        self,
        text: str,
        kind: Literal["message", "document"],
        history: Sequence[str] = (),
    ) -> Verdict: ...


class LlmInjectionClassifier:
    """Rates text with a small model. An unreadable answer counts as a block, never as a pass."""

    def __init__(self, llm: LLM, model: str | None = None) -> None:
        self.llm = llm
        self.model = model

    async def classify(
        self,
        text: str,
        kind: Literal["message", "document"],
        history: Sequence[str] = (),
    ) -> Verdict:
        system = prompts.MESSAGE_SYSTEM if kind == "message" else prompts.DOCUMENT_SYSTEM
        earlier = [m[:HISTORY_CHARS] for m in history if m.strip()][-HISTORY_MESSAGES:]
        context = ""
        if kind == "message" and earlier:
            system += prompts.HISTORY_NOTE
            context = "<history>\n" + "\n".join(f"- {m}" for m in earlier) + "\n</history>\n"
        try:
            data = await chat_json(
                self.llm,
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"{context}<data>\n{text[:MAX_CHARS]}\n</data>"},
                ],
                model=self.model or get_settings().llm_model_small,
                max_tokens=80,
            )
            score = min(max(float(data["score"]), 0.0), 1.0)
            return Verdict(score, str(data.get("category", "other")), str(data.get("reason", "")))
        except (LLMError, KeyError, TypeError, ValueError):
            return Verdict(1.0, UNREADABLE, "the filter returned no usable rating")
