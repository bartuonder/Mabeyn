import json

import pytest

from mabeyn.core.config import get_settings
from mabeyn.ingestion.chunk import count_tokens
from mabeyn.llm.client import ChatMessage
from mabeyn.memory.short_term import MAX_MESSAGE_TOKENS, compact
from tests.fakes import FakeLLM


def conversation(count: int, words: int = 60) -> list[ChatMessage]:
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"message {i} " + "word " * words,
        }
        for i in range(count)
    ]


def summariser(text: str = "Short summary.") -> FakeLLM:
    return FakeLLM(lambda messages, model: text)


async def test_short_conversations_are_left_alone():
    llm = summariser()
    pending = conversation(4, words=20)
    result = await compact(llm, "", 0, pending)
    assert result.history == pending and result.summary == "" and result.summarized == 0
    assert llm.chat_calls == []


async def test_old_messages_are_folded_into_the_summary():
    llm = summariser("The customer asked about order KS-1001.")
    pending = conversation(30)
    result = await compact(llm, "", 0, pending)
    assert result.summary == "The customer asked about order KS-1001."
    assert result.summarized == 30 - len(result.history) > 0
    assert result.history == pending[result.summarized :]
    assert len(result.history) <= get_settings().history_keep_recent


async def test_summary_is_updated_incrementally():
    llm = summariser("Updated summary.")
    await compact(llm, "Earlier: order KS-1001 was discussed.", 10, conversation(30))
    prompt = llm.chat_calls[0][1][1]["content"]
    assert "Earlier: order KS-1001 was discussed." in prompt
    assert "message 0" in prompt


@pytest.mark.parametrize("count", [1, 7, 20, 60, 200])
@pytest.mark.parametrize("words", [10, 200, 2000])
async def test_context_never_exceeds_the_token_budget(count, words):
    settings = get_settings()
    pending = [{"role": "user", "content": "x " * ((i * 37) % words + 1)} for i in range(count)]
    huge_summary = "long " * 5000
    result = await compact(summariser(huge_summary), huge_summary, 0, pending)
    assert result.tokens <= settings.history_token_budget
    assert count_tokens(result.summary) <= settings.summary_max_tokens
    assert all(count_tokens(m["content"]) <= MAX_MESSAGE_TOKENS for m in result.history)


async def test_one_enormous_message_is_clipped():
    result = await compact(summariser(), "", 0, [{"role": "user", "content": "z " * 50000}])
    assert result.tokens <= get_settings().history_token_budget
    assert len(result.history) == 1


async def test_a_failing_summariser_still_respects_the_budget():
    def explode(messages, model):
        raise RuntimeError("model is down")

    result = await compact(FakeLLM(explode), "", 0, conversation(40, words=100))
    assert result.tokens <= get_settings().history_token_budget
    assert "message 0" in result.summary
    assert result.summarized > 0


async def test_empty_summary_from_the_model_falls_back_to_the_transcript():
    result = await compact(summariser("   "), "Old facts.", 0, conversation(40, words=100))
    assert result.summary.startswith("Old facts.")


async def test_summarised_count_tracks_the_database_offset():
    first = await compact(summariser("S1"), "", 0, conversation(30))
    later = conversation(3, words=20)
    second = await compact(summariser("S2"), first.summary, first.summarized, later)
    assert second.summarized == first.summarized
    assert second.history == later
    assert json.dumps(second.summary) == json.dumps("S1")
