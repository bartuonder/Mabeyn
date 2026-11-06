from collections import Counter

import pytest

from evals.datasets import load_questions
from mabeyn.ingestion.parse import parse_markdown
from mabeyn.seed import SEED_ROOT, load_seed_documents

RAG_SETS = [
    "rag.jsonl",
    "rag_en.jsonl",
    "rag_xlang.jsonl",
    "rag_heldout.jsonl",
    "rag_heldout_v2.jsonl",
]


def headings() -> dict[str, set[str]]:
    result = {}
    for seed in load_seed_documents(SEED_ROOT):
        parsed = parse_markdown(seed.data.decode("utf-8"))
        result[seed.filename] = {s.path[-1] for s in parsed.sections if s.path}
    return result


@pytest.mark.parametrize("name", RAG_SETS)
def test_gold_references_point_to_real_sections(name):
    available = headings()
    for question in load_questions(name):
        for gold in question.gold:
            assert gold.doc in available, f"{question.id}: unknown document {gold.doc}"
            assert gold.section in available[gold.doc], (
                f"{question.id}: {gold.doc} has no section {gold.section!r}"
            )


@pytest.mark.parametrize("name", RAG_SETS)
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


def test_full_set_has_eighty_questions_with_sixteen_unanswerable():
    questions = load_questions("rag.jsonl")
    assert len(questions) == 80
    assert sum(not q.answerable for q in questions) == 16
    assert len(load_questions("rag.jsonl", sets=("v1",))) == 30
    assert sum(q.category == "conflict" for q in questions) >= 6


def test_english_set_has_twenty_questions():
    questions = load_questions("rag_en.jsonl")
    assert len(questions) == 20
    assert sum(not q.answerable for q in questions) == 3


def test_conflict_questions_target_the_current_policy():
    for q in load_questions("rag.jsonl"):
        if q.category == "conflict":
            assert all(g.doc != "iade-politikasi-2023.md" for g in q.gold)


def test_every_category_is_known():
    known = {"factual", "numeric", "procedure", "multi", "conflict", "unanswerable"}
    assert set(Counter(q.category for q in load_questions("rag.jsonl"))) <= known


def test_heldout_set_is_separate_from_the_development_set():
    """The held-out questions are never used for tuning, so they must not repeat dev questions."""
    dev = {q.question.casefold() for q in load_questions("rag.jsonl")}
    heldout = load_questions("rag_heldout.jsonl")
    assert len(heldout) == 30 and sum(not q.answerable for q in heldout) == 6
    assert all(q.set == "heldout" for q in heldout)
    assert not dev & {q.question.casefold() for q in heldout}


def test_second_heldout_set_is_fresh():
    """The first held-out set was consumed by a fix, so the second must repeat nothing."""
    seen = {
        q.question.casefold()
        for name in ("rag.jsonl", "rag_en.jsonl", "rag_heldout.jsonl")
        for q in load_questions(name)
    }
    fresh = load_questions("rag_heldout_v2.jsonl")
    assert len(fresh) == 30 and sum(not q.answerable for q in fresh) == 5
    assert all(q.set == "heldout2" for q in fresh)
    assert not seen & {q.question.casefold() for q in fresh}


@pytest.mark.parametrize("name", ["firewall_dev.jsonl", "firewall_heldout.jsonl"])
def test_firewall_sets_are_labelled_and_unique(name):
    import json

    from evals.datasets import DATASETS_DIR

    rows = [json.loads(line) for line in (DATASETS_DIR / name).read_text().splitlines() if line]
    assert len({r["id"] for r in rows}) == len(rows)
    assert {r["label"] for r in rows} == {0, 1}
    assert all(isinstance(r["history"], list) and r["message"].strip() for r in rows)
    assert any(r["history"] for r in rows if r["label"] == 1)


def test_the_firewall_development_and_held_out_sets_do_not_overlap():
    import json

    from evals.datasets import DATASETS_DIR

    def messages(name):
        text = (DATASETS_DIR / name).read_text()
        return {json.loads(line)["message"] for line in text.splitlines() if line}

    assert not messages("firewall_dev.jsonl") & messages("firewall_heldout.jsonl")
