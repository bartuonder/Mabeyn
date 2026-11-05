import argparse
import asyncio
import statistics
import time
from typing import Any

from evals.budget import configure_ledger, ensure_within_budget
from evals.provenance import provenance
from evals.redteam_scoring import load_benign
from evals.tracking import RESULTS_DIR, save_result
from mabeyn.core.config import get_settings
from mabeyn.guard.classifier import LlmInjectionClassifier
from mabeyn.guard.rules import inspect_input
from mabeyn.llm import usage
from mabeyn.llm.client import get_llm


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(q * len(ordered)), len(ordered) - 1)]


async def run(args: argparse.Namespace) -> dict[str, Any]:
    configure_ledger()
    get_settings.cache_clear()
    classifier = LlmInjectionClassifier(get_llm())  # no cache: every call is a real one
    messages = [b.message for b in load_benign()][: args.count]
    rule_ms: list[float] = []
    classifier_ms: list[float] = []
    cost = 0.0
    for text in messages:
        ensure_within_budget()
        started = time.perf_counter()
        inspect_input(text)
        rule_ms.append((time.perf_counter() - started) * 1000)
        with usage.measure() as meter:
            started = time.perf_counter()
            await classifier.classify(text, "message")
            classifier_ms.append((time.perf_counter() - started) * 1000)
        cost += meter.cost_usd
    metrics = {
        "messages": float(len(messages)),
        "rules_p50_ms": statistics.median(rule_ms),
        "rules_p95_ms": percentile(rule_ms, 0.95),
        "classifier_p50_ms": statistics.median(classifier_ms),
        "classifier_p95_ms": percentile(classifier_ms, 0.95),
        "cost_per_message_usd": cost / len(messages),
    }
    result = {
        "name": args.name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provenance": provenance(),
        "config": {"model": get_settings().llm_model_small, "sequential": True},
        "metrics": metrics,
    }
    save_result(args.name, result)
    lines = ["| Metric | Value |", "| --- | ---: |"] + [
        f"| {k} | {v:.4f} |" for k, v in metrics.items()
    ]
    (RESULTS_DIR / f"{args.name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="firewall_latency")
    parser.add_argument("--name", default="firewall-latency")
    parser.add_argument("--count", type=int, default=40)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
