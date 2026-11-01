from collections import Counter

import pytest

from evals.datasets import load_questions
from mabeyn.ingestion.parse import parse_markdown
from mabeyn.seed import SEED_ROOT, load_seed_documents


def headings() -> dict[str, set[str]]:
    result = {}
    for seed in load_seed_documents(SEED_ROOT):
        parsed = parse_markdown(seed.data.decode("utf-8"))
        result[seed.filename] = {s.path[-1] for s in parsed.sections if s.path}
    return result


@pytest.mark.parametrize("name", ["rag.jsonl", "rag_en.jsonl"])
def test_gold_references_point_to_real_sections(name):
    available = headings()
    for question in load_questions(name):
        for gold in question.gold:
            assert gold.doc in available, f"{question.id}: unknown document {gold.doc}"
            assert gold.section in available[gold.doc], (
                f"{question.id}: {gold.doc} has no section {gold.section!r}"
            )


@pytest.mark.parametrize("name", ["rag.jsonl", "rag_en.jsonl"])
def test_ids_are_unique_and_unanswerable_have_no_gold(name):
    questions = load_questions(name)
    assert len({q.id for q in questions}) == len(questions)
    assert len({q.question for q in questions}) == len(questions)
    for q in questions:
        assert q.answerable == bool(q.gold)
        assert q.expected_answer.strip()


def test_v0_set_has_fifty_questions_with_ten_unanswerable():
    v0 = load_questions("rag.jsonl", sets=("v0",))
    assert len(v0) == 50
    assert sum(not q.answerable for q in v0) == 10
    assert all(q.id == f"tr-{i:03d}" for i, q in enumerate(v0, start=1))


def test_conflict_questions_target_the_current_policy():
    for q in load_questions("rag.jsonl"):
        if q.category == "conflict":
            assert all(g.doc != "iade-politikasi-2023.md" for g in q.gold)


def test_every_category_is_known():
    known = {"factual", "numeric", "procedure", "multi", "conflict", "unanswerable"}
    assert set(Counter(q.category for q in load_questions("rag.jsonl"))) <= known
