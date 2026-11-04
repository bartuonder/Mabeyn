import argparse
import asyncio
import json
import math
import time
from typing import Any

from evals.budget import configure_ledger, ensure_within_budget
from evals.cache import CachingLLM
from evals.datasets import DATASETS_DIR
from evals.provenance import provenance
from evals.tracking import RESULTS_DIR, save_result
from mabeyn.cache.answers import normalise, same_question
from mabeyn.core.config import get_settings
from mabeyn.llm.client import get_llm

THRESHOLDS = (0.50, 0.60, 0.70, 0.80, 0.90, 0.93, 0.96, 0.98)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


async def run(args: argparse.Namespace) -> dict[str, Any]:
    configure_ledger()
    get_settings.cache_clear()
    rows = [
        json.loads(line)
        for line in (DATASETS_DIR / "cache_pairs.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    llm = CachingLLM(get_llm())
    ensure_within_budget()
    texts = [normalise(r[side]) for r in rows for side in ("a", "b")]
    vectors = await llm.embed(texts)
    for index, row in enumerate(rows):
        row["similarity"] = cosine(vectors[2 * index], vectors[2 * index + 1])
        row["confirmed"] = await same_question(llm, row["a"], row["b"])
        row["confirmed"] = await same_question(llm, row["a"], row["b"])
    pairs_same = [r for r in rows if r["same"]]
    pairs_different = [r for r in rows if not r["same"]]
    pairs_same = [r for r in rows if r["same"]]
    pairs_different = [r for r in rows if not r["same"]]
    same = [r["similarity"] for r in pairs_same]
    different = [r["similarity"] for r in pairs_different]

    def share(pairs: list[dict[str, Any]], threshold: float, confirmed: bool) -> float:
        hits = [
            r for r in pairs if r["similarity"] >= threshold and (r["confirmed"] or not confirmed)
        ]
        return len(hits) / len(pairs)

    table = [
        {
            "threshold": threshold,
            "similarity_only_paraphrase_hit_rate": share(pairs_same, threshold, False),
            "similarity_only_false_hit_rate": share(pairs_different, threshold, False),
            "confirmed_paraphrase_hit_rate": share(pairs_same, threshold, True),
            "confirmed_false_hit_rate": share(pairs_different, threshold, True),
        }
        for threshold in THRESHOLDS
    ]
    metrics = {
        "paraphrases": float(len(same)),
        "different_questions": float(len(different)),
        "paraphrase_similarity_min": min(same),
        "paraphrase_similarity_median": sorted(same)[len(same) // 2],
        "different_similarity_max": max(different),
        "different_similarity_median": sorted(different)[len(different) // 2],
        "configured_threshold": get_settings().cache_semantic_threshold,
    }
    result = {
        "name": args.name,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provenance": provenance(),
        "config": {"embedding_model": get_settings().embedding_model},
        "metrics": metrics,
        "thresholds": table,
        "pairs": rows,
    }
    path = save_result(args.name, result)
    lines = [
        "| Candidate threshold | Similarity only: paraphrases hit | Similarity only: wrong hits "
        "| With confirmation: paraphrases hit | With confirmation: wrong hits |",
        "| ---: | ---: | ---: | ---: | ---: |",
    ]
    lines += [
        f"| {r['threshold']:.2f} | {r['similarity_only_paraphrase_hit_rate']:.3f} "
        f"| {r['similarity_only_false_hit_rate']:.3f} "
        f"| {r['confirmed_paraphrase_hit_rate']:.3f} | {r['confirmed_false_hit_rate']:.3f} |"
        for r in table
    ]
    (RESULTS_DIR / f"{args.name}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print({k: round(v, 3) for k, v in metrics.items()})
    print(f"saved {path}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(prog="cache_eval")
    parser.add_argument("--name", default="cache-threshold")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
