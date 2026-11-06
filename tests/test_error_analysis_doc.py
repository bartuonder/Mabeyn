import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "error-analysis.md"
PREFIXES = (
    "ablation", "devfull", "english", "firewall", "gate", "heldout", "memory-eval",
    "redteam", "tool-eval", "xlang",
)  # fmt: skip


def test_every_result_the_analysis_cites_exists():
    names = {
        token
        for token in re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", DOC.read_text(encoding="utf-8"))
        if token.startswith(PREFIXES)
    }
    assert names, "the analysis should cite result files"
    stems = {p.name.split("_", 1)[-1].rsplit(".", 1)[0] for p in (ROOT / "evals/results").glob("*")}
    assert not names - stems, f"cited but not in evals/results: {sorted(names - stems)}"


def test_every_question_it_discusses_is_in_a_dataset():
    ids = set(
        re.findall(
            r"`((?:tr|xl|en|h2|tool|toolh2|memh|rt|fw-h)-[a-z]?[0-9]+)`",
            DOC.read_text(encoding="utf-8"),
        )
    )
    known = set()
    for path in (ROOT / "evals/datasets").glob("*.jsonl"):
        known |= set(re.findall(r'"id": "([^"]+)"', path.read_text(encoding="utf-8")))
    assert ids and not ids - known, sorted(ids - known)
