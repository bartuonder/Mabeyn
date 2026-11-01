import json
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from evals.datasets import DATASETS_DIR
from evals.tracking import RESULTS_DIR

CALIBRATION_FILE = DATASETS_DIR / "judge_calibration.jsonl"
SAMPLE_SIZE = 20


@dataclass(frozen=True)
class Agreement:
    n: int
    agreement: float
    kappa: float
    disagreements: tuple[str, ...]


def compute_agreement(rows: list[dict[str, object]]) -> Agreement:
    labeled = [r for r in rows if isinstance(r.get("label"), bool)]
    if not labeled:
        return Agreement(0, float("nan"), float("nan"), ())
    pairs = [(bool(r["judge_correct"]), bool(r["label"])) for r in labeled]
    n = len(pairs)
    observed = sum(j == h for j, h in pairs) / n
    judge_yes = sum(j for j, _ in pairs) / n
    human_yes = sum(h for _, h in pairs) / n
    expected = judge_yes * human_yes + (1 - judge_yes) * (1 - human_yes)
    kappa = 1.0 if expected == 1 else (observed - expected) / (1 - expected)
    disagreements = tuple(str(r["id"]) for r, (j, h) in zip(labeled, pairs, strict=True) if j != h)
    return Agreement(n, observed, kappa, disagreements)


def sample_rows(result_path: Path, seed: int = 7) -> list[dict[str, object]]:
    """Draw a balanced sample (judge-correct vs judge-incorrect) from an evaluation result."""
    questions = json.loads(result_path.read_text(encoding="utf-8"))["questions"]
    rng = random.Random(seed)  # noqa: S311  (reproducible sampling, not security)
    good = [q for q in questions if q["correct"]]
    bad = [q for q in questions if not q["correct"]]
    rng.shuffle(good)
    rng.shuffle(bad)
    take_bad = min(len(bad), SAMPLE_SIZE // 2)
    chosen = bad[:take_bad] + good[: SAMPLE_SIZE - take_bad]
    rng.shuffle(chosen)
    return [
        {
            "id": q["id"],
            "question": q["question"],
            "answer": q["answer"],
            "judge_correct": q["correct"],
            "judge_reason": q["judge_reason"],
            "label": None,
            "reviewed": False,
        }
        for q in chosen
    ]


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "sample":
        rows = sample_rows(Path(sys.argv[2]))
        with CALIBRATION_FILE.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"wrote {len(rows)} rows to {CALIBRATION_FILE}")
        return

    rows = [
        json.loads(line)
        for line in CALIBRATION_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    result = compute_agreement(rows)
    summary = {
        "n_labeled": result.n,
        "agreement": result.agreement,
        "cohens_kappa": result.kappa,
        "disagreements": list(result.disagreements),
        "all_reviewed": all(r.get("reviewed") for r in rows),
    }
    (RESULTS_DIR / "judge-agreement.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
