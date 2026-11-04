import json

from evals.gate import GATE_RESULT_KEYS, THRESHOLDS, Check, evaluate_thresholds, failed, render

BOUNDS = {
    "rag": {"min": {"faithfulness": 0.9}},
    "tools": {"min": {"scenario_pass_rate": 0.8}, "max": {"identity_leaks": 0}},
}


def test_metrics_inside_their_bounds_pass():
    metrics = {
        "rag": {"faithfulness": 0.95},
        "tools": {"scenario_pass_rate": 0.9, "identity_leaks": 0.0},
    }
    assert failed(evaluate_thresholds(metrics, BOUNDS)) == []


def test_a_drop_below_a_minimum_or_above_a_maximum_fails():
    metrics = {
        "rag": {"faithfulness": 0.85},
        "tools": {"scenario_pass_rate": 0.8, "identity_leaks": 1.0},
    }
    names = [c.metric for c in failed(evaluate_thresholds(metrics, BOUNDS))]
    assert names == ["rag.faithfulness", "tools.identity_leaks"]


def test_a_missing_metric_fails_instead_of_passing_silently():
    checks = evaluate_thresholds({"rag": {}}, BOUNDS)
    assert len(failed(checks)) == len(checks) == 3


def test_the_report_marks_failures():
    table = render([Check("rag.faithfulness", 0.8, 0.9, "min"), Check("a.b", 1.0, 0.5, "min")])
    assert "| rag.faithfulness | 0.800 | >= 0.900 | FAIL |" in table
    assert "| a.b | 1.000 | >= 0.500 | pass |" in table


def test_the_committed_thresholds_name_metrics_the_runners_produce():
    bounds = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    for group, names in GATE_RESULT_KEYS.items():
        for kind in ("min", "max"):
            assert set(bounds[group].get(kind, {})) <= names
