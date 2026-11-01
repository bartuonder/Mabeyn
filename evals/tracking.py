import json
import math
import time
from pathlib import Path
from typing import Any

import structlog

from mabeyn.core.tracking import export_tracking_env, wandb_enabled

RESULTS_DIR = Path(__file__).resolve().parent / "results"
WANDB_PROJECT = "mabeyn"

log = structlog.get_logger()


def _clean(value: Any) -> Any:
    """Make results strict JSON: NaN becomes null."""
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def save_result(name: str, result: dict[str, Any], directory: Path = RESULTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}_{name}.json"
    path.write_text(json.dumps(_clean(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def log_wandb(
    name: str, config: dict[str, Any], metrics: dict[str, float], tags: list[str] | None = None
) -> bool:
    """Log a run to Weights & Biases when a key is configured; never fails the evaluation."""
    export_tracking_env()
    if not wandb_enabled():
        return False
    try:
        import wandb

        run = wandb.init(
            project=WANDB_PROJECT,
            name=name,
            config=config,
            tags=tags or [],
            reinit="finish_previous",
        )
        run.log({k: v for k, v in metrics.items() if isinstance(v, int | float)})
        run.finish()
    except Exception as exc:
        log.warning("wandb_logging_failed", error=exc.__class__.__name__)
        return False
    return True
