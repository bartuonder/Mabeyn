import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from mabeyn.core.config import get_settings

# USD per million tokens: (input, output).
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "text-embedding-3-small": (0.02, 0.0),
    "text-embedding-3-large": (0.13, 0.0),
}
# Unknown models are priced high so the budget guard errs on the safe side.
FALLBACK_PRICE = (5.00, 20.00)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    matches = [name for name in PRICES if model.startswith(name)]
    price = PRICES[max(matches, key=len)] if matches else FALLBACK_PRICE
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


@dataclass
class UsageMeter:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_adjustment_s: float = 0.0

    def add(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += cost_usd(model, input_tokens, output_tokens)


_current: ContextVar[UsageMeter | None] = ContextVar("usage_meter", default=None)
_ledger_lock = threading.Lock()


@contextmanager
def measure() -> Iterator[UsageMeter]:
    """Collect usage of every LLM call made inside the block."""
    meter = UsageMeter()
    token = _current.set(meter)
    try:
        yield meter
    finally:
        _current.reset(token)


def current_meter() -> UsageMeter | None:
    return _current.get()


def adjust_latency(seconds: float) -> None:
    """Account for time a cached call would have taken (negative if it took longer than replay)."""
    meter = _current.get()
    if meter is not None:
        meter.latency_adjustment_s += seconds


def record(model: str, input_tokens: int, output_tokens: int, *, billed: bool = True) -> None:
    """Add a call to the active meter; `billed=False` replays a cached call without spending."""
    meter = _current.get()
    if meter is not None:
        meter.add(model, input_tokens, output_tokens)

    ledger = get_settings().llm_ledger_path
    if billed and ledger:
        entry = {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd(model, input_tokens, output_tokens),
        }
        with _ledger_lock, Path(ledger).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def ledger_total(path: str | Path) -> float:
    target = Path(path)
    if not target.exists():
        return 0.0
    total = 0.0
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += float(json.loads(line)["cost_usd"])
    return total
