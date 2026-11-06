import json

import pytest

from mabeyn.core.config import get_settings
from mabeyn.llm import usage


def test_cost_uses_longest_matching_model_prefix():
    mini = usage.cost_usd("gpt-4.1-mini-2025-04-14", 1_000_000, 1_000_000)
    assert mini == pytest.approx(0.40 + 1.60)
    full = usage.cost_usd("gpt-4.1", 1_000_000, 0)
    assert full == pytest.approx(2.00)


def test_unknown_model_uses_conservative_price():
    assert usage.cost_usd("brand-new-model", 1_000_000, 0) == pytest.approx(5.00)


def test_measure_collects_usage_inside_block_only():
    usage.record("gpt-4.1-nano", 100, 10)
    with usage.measure() as meter:
        usage.record("gpt-4.1-nano", 1_000_000, 0)
        usage.record("gpt-4.1-nano", 0, 1_000_000)
    usage.record("gpt-4.1-nano", 500, 500)
    assert meter.calls == 2
    assert meter.input_tokens == 1_000_000
    assert meter.cost_usd == pytest.approx(0.10 + 0.40)


def test_ledger_roundtrip(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("LLM_LEDGER_PATH", str(ledger))
    get_settings.cache_clear()
    try:
        usage.record("gpt-4.1-mini", 1_000_000, 0)
        usage.record("gpt-4.1-mini", 0, 1_000_000)
    finally:
        monkeypatch.delenv("LLM_LEDGER_PATH")
        get_settings.cache_clear()
    assert len(ledger.read_text().splitlines()) == 2
    assert json.loads(ledger.read_text().splitlines()[0])["model"] == "gpt-4.1-mini"
    assert usage.ledger_total(ledger) == pytest.approx(2.0)


def test_ledger_total_of_missing_file_is_zero(tmp_path):
    assert usage.ledger_total(tmp_path / "missing.jsonl") == 0.0


def test_a_nested_measure_adds_its_usage_to_the_outer_one():
    with usage.measure() as outer:
        usage.record("gpt-4o-mini", 100, 10)
        with usage.measure() as inner:
            usage.record("gpt-4.1-mini", 1000, 100)
            assert outer.calls == 1  # not yet: the inner block is still open
        assert inner.calls == 1 and outer.calls == 2
    assert outer.input_tokens == 1100
    assert outer.cost_usd == inner.cost_usd + usage.cost_usd("gpt-4o-mini", 100, 10)
