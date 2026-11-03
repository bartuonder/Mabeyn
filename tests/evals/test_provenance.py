import math

from evals.metrics import wilson_interval
from evals.provenance import provenance
from evals.reporting import markdown_table


def test_results_record_commit_prompts_and_models():
    info = provenance()
    assert set(info["prompt_versions"]) == {"rag", "agents", "memory", "guard"}
    assert info["models"]["generation"] and info["models"]["judge"]
    assert info["commit"] is None or len(info["commit"]) == 40


def test_wilson_interval_behaves_at_the_edges():
    low, high = wilson_interval(78, 80)
    assert 0.90 < low < 0.975 < high <= 1.0
    assert wilson_interval(0, 10)[0] == 0.0
    assert wilson_interval(10, 10)[1] == 1.0
    assert all(math.isnan(v) for v in wilson_interval(0, 0))
    small = wilson_interval(15, 15)
    assert small[0] < 0.85  # fifteen perfect answers do not prove a perfect system


def test_tables_can_show_the_interval():
    metrics = {"accuracy": 0.9, "accuracy_ci95_low": 0.8, "accuracy_ci95_high": 0.95}
    plain = markdown_table([("row", metrics)], [("Accuracy", "accuracy", 3)])
    with_ci = markdown_table([("row", metrics)], [("Accuracy", "accuracy", 3)], intervals=True)
    assert "0.900 |" in plain and "[" not in plain
    assert "0.900 [0.80-0.95]" in with_ci
