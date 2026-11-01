import importlib
import subprocess
from pathlib import Path
from typing import Any

from mabeyn.core.config import get_settings
from mabeyn.prompt_registry import PROMPT_MODULES

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str | None:
    try:
        done = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip()


def provenance() -> dict[str, Any]:
    """Which code, prompts and models produced a result."""
    settings = get_settings()
    status = _git("status", "--porcelain")
    return {
        "commit": _git("rev-parse", "HEAD"),
        "dirty": bool(status) if status is not None else None,
        "prompt_versions": {
            name.rsplit(".", 2)[-2]: importlib.import_module(name).PROMPT_VERSION
            for name in PROMPT_MODULES
        },
        "models": {
            "generation": settings.llm_model,
            "small": settings.llm_model_small,
            "judge": settings.judge_model,
            "embedding": settings.embedding_model,
        },
    }
