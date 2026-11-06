import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from evals.budget import configure_ledger, ensure_within_budget
from evals.cache import CachingLLM
from evals.datasets import DATASETS_DIR
from evals.metrics import wilson_interval
from evals.provenance import provenance
from evals.runner import drop_tenant
from evals.tracking import RESULTS_DIR, save_result
from mabeyn.core.config import get_settings
from mabeyn.db.provisioning import create_tenant
from mabeyn.guard.basic import check_input
from mabeyn.guard.classifier import LlmInjectionClassifier
from mabeyn.guard.firewall import Firewall
from mabeyn.llm import usage
from mabeyn.llm.client import get_llm

SETS = {"dev": "firewall_dev.jsonl", "heldout": "firewall_heldout.jsonl"}


def load(name: str, path: Path | None = None) -> list[dict[str, Any]]:
    text = ((path or DATASETS_DIR) / SETS[name]).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def interval(successes: int, total: int) -> list[float]:
    low, high = wilson_interval(successes, total)
    return [round(low, 3), round(high, 3)]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attacks = [r for r in rows if r["label"] == 1]
    benign = [r for r in rows if r["label"] == 0]
    caught = sum(r["blocked"] for r in attacks)
    wrong = sum(r["blocked"] for r in benign)
    groups: dict[str, dict[str, int]] = {}
    for r in rows:
        g = groups.setdefault(r["group"], {"blocked": 0, "total": 0})
        g["total"] += 1
        g["blocked"] += int(r["blocked"])
    return {
        "attacks": len(attacks),
        "attacks_blocked": caught,
        "detection_rate": caught / len(attacks),
        "detection_ci95": interval(caught, len(attacks)),
        "benign": len(benign),
        "benign_blocked": wrong,
        "false_block_rate": wrong / len(benign),
        "false_block_ci95": interval(wrong, len(benign)),
        "groups": groups,
        "missed": [r["id"] for r in attacks if not r["blocked"]],
        "false_blocks": [r["id"] for r in benign if r["blocked"]],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    configure_ledger()
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_async_engine(settings.admin_database_url)
    suffix = uuid.uuid4().hex[:8]
    tenant_id = await create_tenant(
        engine, "Firewall eval", f"fw-eval-{suffix}", f"fw-{suffix}@example.com", "eval-password-1"
    )
    llm = CachingLLM(get_llm())
    firewall = Firewall(LlmInjectionClassifier(llm))
    try:
        rows = []
        cost = 0.0
        for item in load(args.set):
            ensure_within_budget()
            clean = check_input(item["message"]).text
            extra = {"history": item["history"]} if args.history else {}
            with usage.measure() as meter:
                decision = await firewall.inspect_input(
                    tenant_id, None, item["message"], clean, **extra
                )
            cost += meter.cost_usd
            rows.append(
                {
                    "id": item["id"],
                    "group": item["group"],
                    "label": item["label"],
                    "blocked": not decision.allowed,
                    "category": decision.category,
                    "score": decision.score,
                }
            )
    finally:
        await drop_tenant(engine, tenant_id)
    metrics = summarize(rows)
    result = {
        "name": args.name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provenance": provenance(),
        "config": {"set": args.set, "history": args.history, "model": settings.llm_model_small},
        "cache": {"hits": llm.hits, "misses": llm.misses},
        "cost_usd": cost,
        "metrics": metrics,
        "rows": rows,
    }
    save_result(args.name, result)
    table = [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| attacks blocked | {metrics['attacks_blocked']} of {metrics['attacks']} "
        f"{metrics['detection_ci95']} |",
        f"| harmless blocked | {metrics['benign_blocked']} of {metrics['benign']} "
        f"{metrics['false_block_ci95']} |",
    ]
    table += [
        f"| group {g} | {v['blocked']} of {v['total']} blocked |"
        for g, v in metrics["groups"].items()
    ]
    (RESULTS_DIR / f"{args.name}.md").write_text("\n".join(table) + "\n", encoding="utf-8")
    print("\n".join(table))
    if args.set == "dev":  # the held-out lists stay in the result file and are not read
        print("missed:", metrics["missed"])
        print("false blocks:", metrics["false_blocks"])
    print(f"cache hits {llm.hits} misses {llm.misses}; cost ${cost:.4f}")
    await engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="firewall_eval")
    parser.add_argument("--set", choices=sorted(SETS), default="dev")
    parser.add_argument("--name", default="firewall-eval")
    parser.add_argument("--history", action="store_true", help="give the layer the earlier turns")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
