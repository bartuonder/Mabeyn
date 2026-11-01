import os
from pathlib import Path

from mabeyn.core.config import get_settings
from mabeyn.llm.usage import ledger_total

LEDGER_PATH = Path(__file__).resolve().parent / ".ledger.jsonl"
DEFAULT_LIMIT_USD = 4.0


class BudgetExceededError(Exception):
    pass


def budget_limit() -> float:
    return float(os.environ.get("EVAL_BUDGET_USD", DEFAULT_LIMIT_USD))


def configure_ledger() -> None:
    """Route every LLM call of this process into the shared spend ledger."""
    os.environ["LLM_LEDGER_PATH"] = str(LEDGER_PATH)
    get_settings.cache_clear()


def spent_usd() -> float:
    return ledger_total(LEDGER_PATH)


def ensure_within_budget() -> None:
    spent, limit = spent_usd(), budget_limit()
    if spent >= limit:
        raise BudgetExceededError(f"spent ${spent:.2f} of the ${limit:.2f} evaluation budget")
