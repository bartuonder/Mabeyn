import json

import pytest

from evals.cache import CachingLLM
from mabeyn.core.config import get_settings
from mabeyn.llm import usage
from tests.fakes import FakeLLM

MESSAGES = [{"role": "user", "content": "hello"}]


async def test_chat_is_cached_and_replays_usage_without_spending(tmp_path):
    inner = FakeLLM(lambda m, model: "answer")
    llm = CachingLLM(inner, tmp_path)
    with usage.measure() as first:
        await llm.chat(MESSAGES, model="gpt-4.1-nano")
    with usage.measure() as second:
        replay = await llm.chat(MESSAGES, model="gpt-4.1-nano")

    assert replay.text == "answer"
    assert len(inner.chat_calls) == 1
    assert (llm.hits, llm.misses) == (1, 1)
    assert second.input_tokens == replay.input_tokens > 0
    assert first.cost_usd == pytest.approx(second.cost_usd) or first.cost_usd == 0


async def test_cache_key_covers_model_and_messages(tmp_path):
    inner = FakeLLM(lambda m, model: model)
    llm = CachingLLM(inner, tmp_path)
    await llm.chat(MESSAGES, model="gpt-4.1-nano")
    await llm.chat(MESSAGES, model="gpt-4.1-mini")
    await llm.chat([{"role": "user", "content": "other"}], model="gpt-4.1-mini")
    assert len(inner.chat_calls) == 3


async def test_hits_do_not_touch_the_spend_ledger(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("LLM_LEDGER_PATH", str(ledger))
    get_settings.cache_clear()
    try:
        llm = CachingLLM(FakeLLM(lambda m, model: "x"), tmp_path / "cache")
        await llm.chat(MESSAGES, model="gpt-4.1-nano")
        with usage.measure():
            await llm.chat(MESSAGES, model="gpt-4.1-nano")
    finally:
        monkeypatch.delenv("LLM_LEDGER_PATH")
        get_settings.cache_clear()
    assert len(ledger.read_text().splitlines()) == 1


async def test_embeddings_are_cached_per_text(tmp_path):
    inner = FakeLLM()
    llm = CachingLLM(inner, tmp_path)
    first = await llm.embed(["alpha", "beta"])
    second = await llm.embed(["beta", "gamma", "alpha"])
    assert inner.embed_calls == [["alpha", "beta"], ["gamma"]]
    assert second[0] == first[1] and second[2] == first[0]
    assert len(second) == 3


async def test_stream_hit_replays_the_full_text(tmp_path):
    inner = FakeLLM(lambda m, model: "one two three")
    llm = CachingLLM(inner, tmp_path)
    first = [p async for p in llm.stream(MESSAGES)]
    second = [p async for p in llm.stream(MESSAGES)]
    assert "".join(first).strip() == "one two three"
    assert "".join(second) == "".join(first)
    assert len(inner.chat_calls) == 1


async def test_cached_latency_is_replayed_into_the_meter(tmp_path):
    llm = CachingLLM(FakeLLM(lambda m, model: "x"), tmp_path)
    await llm.chat(MESSAGES)
    entry_path = next(tmp_path.rglob("*.json"))
    entry = json.loads(entry_path.read_text())
    entry["latency"] = 1.5
    entry_path.write_text(json.dumps(entry))
    with usage.measure() as meter:
        await llm.chat(MESSAGES)
    assert meter.latency_adjustment_s == pytest.approx(1.5)
