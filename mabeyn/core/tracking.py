import os
from pathlib import Path

from dotenv import dotenv_values

TRACKING_PREFIXES = ("LANGCHAIN_", "LANGSMITH_", "WANDB_")


def export_tracking_env(env_file: str | Path = ".env") -> None:
    """Copy tracking settings from .env into the process environment (existing values win)."""
    path = Path(env_file)
    if not path.exists():
        return
    for key, value in dotenv_values(path).items():
        if value and key.startswith(TRACKING_PREFIXES):
            os.environ.setdefault(key, value)


def langsmith_enabled() -> bool:
    key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    flag = os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2")
    return bool(key) and (flag or "").lower() == "true"


def wandb_enabled() -> bool:
    return bool(os.environ.get("WANDB_API_KEY"))
