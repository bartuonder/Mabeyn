"""Run the load test with the answer cache off and on and write one comparison.

    uv run python -m loadtest.run --users 50 --run-time 40

The API runs as a local process so the cache setting can change between runs; the docker `api`
container is stopped first. Needs the demo tenant (`mabeyn.cli seed-kargoshop`) and real OpenAI
credentials. Cost and hit rate come from the tenant's own usage report.
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from evals.provenance import provenance
from evals.tracking import RESULTS_DIR, save_result

HOST = "http://localhost:8000"
ROOT = Path(__file__).resolve().parent.parent
WARMUP = [
    "How many days do I have to return a product?",
    "How long does delivery to KKTC take?",
    "Can I pay in instalments?",
    "How do I track my parcel?",
    "How do I delete my account?",
    "Do you ship to Germany?",
    "What does the warranty cover?",
    "How can I contact customer service?",
    "How do gift cards work?",
    "When are loyalty points added?",
]


def wait_for_api(timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{HOST}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(1)
    raise RuntimeError("the API did not become healthy")


def admin_headers(email: str, password: str) -> dict[str, str]:
    response = httpx.post(f"{HOST}/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def usage_totals(headers: dict[str, str]) -> dict[str, float]:
    report = httpx.get(f"{HOST}/usage?days=1", headers=headers, timeout=30).json()
    days = report["days"]
    return {
        "messages": float(sum(d["messages"] for d in days)),
        "cost_usd": float(sum(d["cost_usd"] for d in days)),
        "cached_messages": float(sum(d["cached_messages"] for d in days)),
    }


def warm_up(api_key: str) -> None:
    session = httpx.post(
        f"{HOST}/sessions", json={"customer_id": "load-warmup"}, headers={"X-API-Key": api_key}
    ).json()["token"]
    for question in WARMUP:
        httpx.post(
            f"{HOST}/chat",
            json={"message": question},
            headers={"Authorization": f"Bearer {session}"},
            timeout=120,
        )


def read_stats(directory: str) -> dict[str, dict[str, float]]:
    rows: dict[str, dict[str, float]] = {}
    with Path(directory, "run_stats.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows[row["Name"]] = {
                "requests": float(row["Request Count"]),
                "failures": float(row["Failure Count"]),
                "p50_ms": float(row["50%"]),
                "p95_ms": float(row["95%"]),
                "mean_ms": float(row["Average Response Time"]),
            }
    return rows


def run_mode(
    mode: str, args: argparse.Namespace, key: str, headers: dict[str, str]
) -> dict[str, Any]:
    env = {
        **os.environ,
        "CACHE_ENABLED": "true" if mode == "on" else "false",
        # The test is about latency, so the abuse limits must not interfere.
        "CHAT_RATE_PER_CUSTOMER": "100000",
        "CHAT_RATE_PER_KEY": "100000",
        "CHAT_RATE_PER_TENANT": "100000",
        "SESSION_RATE_PER_ADDRESS": "100000",
        "SESSION_RATE_PER_KEY": "100000",
        "LANGSMITH_TRACING": "false",
        "LANGCHAIN_TRACING_V2": "false",
    }
    api = subprocess.Popen(  # noqa: S603
        ["uv", "run", "python", "-m", "apps.api"],  # noqa: S607
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_api()
        headers = admin_headers(args.email, args.password)
        key = httpx.post(f"{HOST}/api-keys", json={"name": f"load-{mode}"}, headers=headers).json()[
            "key"
        ]
        if mode == "on":
            warm_up(key)
        before = usage_totals(headers)
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "uv",
                    "run",
                    "--group",
                    "loadtest",
                    "locust",
                    "-f",
                    "loadtest/locustfile.py",
                    "--headless",
                    "-u",
                    str(args.users),
                    "-r",
                    str(args.spawn_rate),
                    "-t",
                    f"{args.run_time}s",
                    "--host",
                    HOST,
                    "--csv",
                    f"{directory}/run",
                    "--only-summary",
                ],
                cwd=ROOT,
                env={**env, "LOAD_API_KEY": key},
                check=True,
            )
            stats = read_stats(directory)
        after = usage_totals(headers)
    finally:
        api.terminate()
        api.wait(timeout=30)
    delta = {k: after[k] - before[k] for k in after}
    served = max(delta["messages"], 1.0)
    return {
        "mode": mode,
        "stats": stats,
        "messages": delta["messages"],
        "cache_hit_rate": delta["cached_messages"] / served,
        "cost_usd": delta["cost_usd"],
        "cost_per_message_usd": delta["cost_usd"] / served,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="loadtest")
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--spawn-rate", type=int, default=10)
    parser.add_argument("--run-time", type=int, default=40)
    parser.add_argument("--email", default="admin@kargoshop.example")
    parser.add_argument("--password", default=os.environ.get("DEMO_PASSWORD", ""))
    parser.add_argument("--name", default="loadtest")
    args = parser.parse_args()
    if not args.password:
        sys.exit("pass --password or set DEMO_PASSWORD (the demo tenant admin password)")

    subprocess.run(["docker", "compose", "stop", "api"], cwd=ROOT, check=False)  # noqa: S603, S607
    results = [run_mode(mode, args) for mode in ("off", "on")]

    summary = {
        "name": args.name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provenance": provenance(),
        "config": {"users": args.users, "run_time_s": args.run_time, "host": HOST},
        "results": results,
    }
    path = save_result(args.name, summary)
    lines = [
        "| Cache | Requests | Failures | p50 (s) | p95 (s) | Hit rate | Cost per message ($) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        total = r["stats"]["Aggregated"]
        lines.append(
            f"| {r['mode']} | {total['requests']:.0f} | {total['failures']:.0f} "
            f"| {total['p50_ms'] / 1000:.2f} | {total['p95_ms'] / 1000:.2f} "
            f"| {r['cache_hit_rate']:.2f} | {r['cost_per_message_usd']:.5f} |"
        )
    (RESULTS_DIR / f"{args.name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(json.dumps({r["mode"]: r["stats"] for r in results}, indent=1)[:2000])
    print(f"saved {path}")


if __name__ == "__main__":
    main()
