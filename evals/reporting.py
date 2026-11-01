import time
from collections.abc import Sequence
from typing import Any

from evals.provenance import provenance
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
        "provenance": provenance(),
        "config": config,
        "metrics": summarize(results, retrieval_only=retrieval_only),
        "accuracy_by_category": {} if retrieval_only else accuracy_by_category(results),
        "questions": to_json(results),
    }


def _format(value: float | None, digits: int) -> str:
    return "-" if value is None or value != value else f"{value:.{digits}f}"


def _cell(metrics: dict[str, float], key: str, digits: int, interval: bool) -> str:
    text = _format(metrics.get(key), digits)
    low, high = metrics.get(f"{key}_ci95_low"), metrics.get(f"{key}_ci95_high")
    if interval and low is not None and high is not None and text != "-":
        text += f" [{low:.2f}-{high:.2f}]"
    return text


def markdown_table(
    rows: Sequence[tuple[str, dict[str, float]]],
    columns: Sequence[tuple[str, str, int]],
    intervals: bool = False,
) -> str:
    """Render `(label, metrics)` rows; columns are `(header, metric key, decimals)`.

    With `intervals`, metrics that have a 95% interval show it next to the value.
    """
    header = "| Configuration | " + " | ".join(c[0] for c in columns) + " |"
    divider = "| --- | " + " | ".join("---:" for _ in columns) + " |"
    lines = [header, divider]
    for label, metrics in rows:
        cells = [_cell(metrics, key, digits, intervals) for _, key, digits in columns]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)
