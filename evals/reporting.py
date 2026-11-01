import time
from collections.abc import Sequence
from typing import Any

from evals.runner import QuestionResult, accuracy_by_category, summarize, to_json


def build_result(
    name: str,
    config: dict[str, Any],
    results: list[QuestionResult],
    retrieval_only: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": config,
        "metrics": summarize(results, retrieval_only=retrieval_only),
        "accuracy_by_category": {} if retrieval_only else accuracy_by_category(results),
        "questions": to_json(results),
    }


def _format(value: float | None, digits: int) -> str:
    return "-" if value is None or value != value else f"{value:.{digits}f}"


def markdown_table(
    rows: Sequence[tuple[str, dict[str, float]]], columns: Sequence[tuple[str, str, int]]
) -> str:
    """Render `(label, metrics)` rows; columns are `(header, metric key, decimals)`."""
    header = "| Configuration | " + " | ".join(c[0] for c in columns) + " |"
    divider = "| --- | " + " | ".join("---:" for _ in columns) + " |"
    lines = [header, divider]
    for label, metrics in rows:
        cells = [_format(metrics.get(key), digits) for _, key, digits in columns]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
