"""CI gate: a quick RAG evaluation and a quick tool-use evaluation with pass or fail thresholds.

uv run python -m evals.gate                 run both and exit 1 when a threshold is missed
uv run python -m evals.gate --check FILE    only compare an existing result file
"""

import argparse
import asyncio
import json
import sys
import time
from argparse import Namespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.runners import rag_eval, tool_eval
from evals.tracking import RESULTS_DIR

ROOT = Path(__file__).resolve().parent
SETS = ROOT / "gate_sets.json"
THRESHOLDS = ROOT / "gate_thresholds.json"
# Metrics a threshold may refer to, so a typo cannot turn a check into a silent failure.
GATE_RESULT_KEYS = {
    "rag": {
        "accuracy",
        "accuracy_answerable",
        "accuracy_unanswerable",
        "faithfulness",
        "mrr",
        "recall@5",
    },
    "tools": {
        "scenario_pass_rate",
        "tool_selection_accuracy",
        "argument_accuracy",
        "unnecessary_call_rate",
        "sensitive_approval_rate",
        "sensitive_executed_without_approval",
        "identity_leaks",
        "outcome_accuracy",
        "language_accuracy",
    },
}


@dataclass(frozen=True)
class Check:
    metric: str
    value: float
    bound: float
    kind: str  # "min" or "max"

    @property
    def passed(self) -> bool:
        return self.value >= self.bound if self.kind == "min" else self.value <= self.bound


def evaluate_thresholds(
    metrics: dict[str, dict[str, float]], thresholds: dict[str, dict[str, dict[str, float]]]
) -> list[Check]:
    """Compare each measured metric with its minimum or maximum. A missing metric fails."""
    checks: list[Check] = []
    for group, bounds in thresholds.items():
        measured = metrics.get(group, {})
        for kind in ("min", "max"):
            for name, bound in bounds.get(kind, {}).items():
                value = measured.get(name)
                checks.append(
                    Check(f"{group}.{name}", float("nan") if value is None else value, bound, kind)
                )
    return checks


def failed(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not (c.value == c.value and c.passed)]


def render(checks: list[Check]) -> str:
    lines = ["| Check | Value | Limit | Result |", "| --- | ---: | ---: | --- |"]
    for c in checks:
        verdict = "pass" if c not in failed(checks) else "FAIL"
        sign = ">=" if c.kind == "min" else "<="
        lines.append(f"| {c.metric} | {c.value:.3f} | {sign} {c.bound:.3f} | {verdict} |")
    return "\n".join(lines)


async def measure() -> dict[str, dict[str, float]]:
    sets: dict[str, Any] = json.loads(SETS.read_text(encoding="utf-8"))
    rag = await rag_eval.run(
        Namespace(
            name="gate-rag",
            config=sets["rag"]["config"],
            dataset=sets["rag"]["dataset"],
            sets=sets["rag"]["sets"],
            limit=None,
            chunk="default",
            embedding=None,
            concurrency=3,
            retrieval_only=False,
            keep_tenant=False,
        )
    )
    tools = await tool_eval.run(
        Namespace(
            name="gate-tools",
            dataset=sets["tools"]["dataset"],
            limit=None,
            categories=None,
            ids=sets["tools"]["ids"],
        )
    )
    return {"rag": rag["metrics"], "tools": tools["metrics"]}


def main() -> None:
    parser = argparse.ArgumentParser(prog="gate")
    parser.add_argument("--check", help="compare an existing gate result file")
    args = parser.parse_args()
    thresholds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    if args.check:
        metrics = json.loads(Path(args.check).read_text(encoding="utf-8"))["metrics"]
    else:
        metrics = asyncio.run(measure())
    checks = evaluate_thresholds(metrics, thresholds)
    table = render(checks)
    print("\n" + table)
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"{stamp}_gate.json").write_text(
        json.dumps({"metrics": metrics, "checks": [c.__dict__ for c in checks]}, indent=2) + "\n",
        encoding="utf-8",
    )
    (RESULTS_DIR / "gate.md").write_text(table + "\n", encoding="utf-8")
    if failed(checks):
        print(f"\ngate FAILED: {', '.join(c.metric for c in failed(checks))}")
        sys.exit(1)
    print("\ngate passed")


if __name__ == "__main__":
    main()
