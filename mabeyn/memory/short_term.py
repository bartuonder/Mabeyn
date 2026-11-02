from collections.abc import Sequence
from dataclasses import dataclass

import structlog

from mabeyn.core.config import get_settings
from mabeyn.ingestion.chunk import count_tokens, truncate_tokens
from mabeyn.llm.client import LLM, ChatMessage
from mabeyn.memory import prompts

log = structlog.get_logger()

MAX_MESSAGE_TOKENS = 400
MESSAGE_OVERHEAD_TOKENS = 4
WORDS_PER_TOKEN = 0.75


@dataclass(frozen=True)
class Compacted:
    """Conversation context for one turn: a summary of old messages plus the recent ones."""

    summary: str
    summarized: int
    history: list[ChatMessage]

    @property
    def tokens(self) -> int:
        return count_tokens(self.summary) + _messages_tokens(self.history)


def _message_tokens(message: ChatMessage) -> int:
    return count_tokens(message["content"]) + MESSAGE_OVERHEAD_TOKENS


def _messages_tokens(messages: Sequence[ChatMessage]) -> int:
    return sum(_message_tokens(m) for m in messages)


def _clip(message: ChatMessage) -> ChatMessage:
    return {
        "role": message["role"],
        "content": truncate_tokens(message["content"], MAX_MESSAGE_TOKENS),
    }


async def _summarise(llm: LLM, summary: str, folded: Sequence[ChatMessage], limit: int) -> str:
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in folded)
    try:
        result = await llm.chat(
            [
                {
                    "role": "system",
                    "content": prompts.SUMMARY_SYSTEM.format(words=int(limit * WORDS_PER_TOKEN)),
                },
                {
                    "role": "user",
                    "content": prompts.SUMMARY_USER.format(
                        summary=summary or "(none)", messages=transcript
                    ),
                },
            ],
            model=get_settings().llm_model_small,
            max_tokens=limit,
        )
        merged = result.text.strip()
    except Exception:
        log.warning("summary_failed", exc_info=True)
        merged = ""
    if not merged:
        merged = f"{summary}\n{transcript}".strip()
    return truncate_tokens(merged, limit)


async def compact(
    llm: LLM, summary: str, summarized: int, pending: Sequence[ChatMessage]
) -> Compacted:
    """Fold old messages into the summary so summary plus history stay inside the token budget.

    `pending` holds every message that is not part of the summary yet, oldest first.
    """
    settings = get_settings()
    budget, limit = settings.history_token_budget, settings.summary_max_tokens
    messages = [_clip(m) for m in pending]
    summary = truncate_tokens(summary, limit)

    if count_tokens(summary) + _messages_tokens(messages) <= budget:
        return Compacted(summary, summarized, messages)

    room = budget - limit
    keep: list[ChatMessage] = []
    used = 0
    for message in reversed(messages):
        cost = _message_tokens(message)
        if len(keep) >= settings.history_keep_recent or (keep and used + cost > room):
            break
        keep.insert(0, message)
        used += cost
    folded = messages[: len(messages) - len(keep)]
    if folded:
        summary = await _summarise(llm, summary, folded, limit)
    return Compacted(summary, summarized + len(folded), keep)
