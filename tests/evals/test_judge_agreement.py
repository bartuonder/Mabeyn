import json
import math

import pytest

from evals.runners.judge_agreement import compute_agreement, sample_rows


def rows(pairs: list[tuple[bool, bool | None]]) -> list[dict[str, object]]:
    return [
        {"id": f"q{i}", "judge_correct": judge, "label": label}
        for i, (judge, label) in enumerate(pairs)
    ]


def test_perfect_agreement_has_kappa_one():
    result = compute_agreement(rows([(True, True), (False, False), (True, True), (False, False)]))
    assert result.n == 4 and result.agreement == 1.0 and result.kappa == 1.0
    assert result.disagreements == ()


def test_disagreements_are_listed_and_reduce_agreement():
    result = compute_agreement(rows([(True, True), (True, False), (False, False), (False, True)]))
    assert result.agreement == 0.5
    assert result.disagreements == ("q1", "q3")
    assert result.kappa == pytest.approx(0.0)


def test_unlabeled_rows_are_ignored():
    result = compute_agreement(rows([(True, True), (False, None)]))
    assert result.n == 1


def test_no_labels_gives_nan():
    result = compute_agreement(rows([(True, None)]))
    assert result.n == 0 and math.isnan(result.agreement)


def test_sample_is_balanced_and_reproducible(tmp_path):
    questions = [
        {
            "id": f"q{i}",
            "question": "?",
            "answer": "a",
            "correct": i % 5 != 0,
            "judge_reason": "",
        }
        for i in range(60)
    ]
    path = tmp_path / "result.json"
    path.write_text(json.dumps({"questions": questions}))
    first, second = sample_rows(path), sample_rows(path)
    assert first == second and len(first) == 20
    assert sum(not r["judge_correct"] for r in first) == 10
    assert all(r["label"] is None and r["reviewed"] is False for r in first)
