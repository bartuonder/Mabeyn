import os
from pathlib import Path

from dotenv import dotenv_values

TRACKING_PREFIXES = ("LANGCHAIN_", "LANGSMITH_", "WANDB_")
# Share of runs LangSmith records, so a busy API cannot use up a monthly trace quota.
DEFAULT_SAMPLE_RATE = "0.1"


def export_tracking_env(env_file: str | Path = ".env") -> None:
    """Copy tracking settings from .env into the process environment (existing values win).

    `TRACING_SAMPLE_RATE` (0 to 1, default 0.1) sets the share of runs LangSmith records.
    """
    path = Path(env_file)
    if path.exists():
        for key, value in dotenv_values(path).items():
            if value and key.startswith(TRACKING_PREFIXES):
                os.environ.setdefault(key, value)
    rate = os.environ.get("TRACING_SAMPLE_RATE", DEFAULT_SAMPLE_RATE)
    os.environ.setdefault("LANGSMITH_TRACING_SAMPLING_RATE", rate)
    os.environ.setdefault("LANGCHAIN_TRACING_SAMPLING_RATE", rate)


def langsmith_enabled() -> bool:
    key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    flag = os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2")
    return bool(key) and (flag or "").lower() == "true"


def wandb_enabled() -> bool:
    return bool(os.environ.get("WANDB_API_KEY"))
