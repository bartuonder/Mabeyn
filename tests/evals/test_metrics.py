import math
import uuid

import pytest

from evals.datasets import Gold
from evals.metrics import chunk_matches, mean, percentile, recall_at_k, reciprocal_rank
from mabeyn.rag.types import RetrievedChunk


def chunk(doc: str, section: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        document_title=doc,
        source_uri=doc,
        section=section,
        page=None,
        content="",
        context_header="",
        effective_date=None,
        score=1.0,
    )


A = Gold("a.md", "Returns")
B = Gold("b.md", "Shipping")


def test_match_requires_document_and_section():
    assert chunk_matches(chunk("a.md", "Policy > Returns"), A)
    assert not chunk_matches(chunk("b.md", "Policy > Returns"), A)
    assert not chunk_matches(chunk("a.md", "Policy > Refunds"), A)


def test_merged_chunks_match_any_of_their_sections():
    merged = chunk("a.md", "Policy > Refunds | Policy > Returns | Policy > Exceptions")
    assert chunk_matches(merged, A)


def test_recall_counts_gold_items_found_within_k():
    ranked = [chunk("x.md", "Other"), chunk("a.md", "Returns"), chunk("b.md", "Shipping")]
    assert recall_at_k(ranked, [A, B], 1) == 0.0
    assert recall_at_k(ranked, [A, B], 2) == 0.5
    assert recall_at_k(ranked, [A, B], 3) == 1.0


def test_recall_and_mrr_are_nan_without_gold():
    assert math.isnan(recall_at_k([chunk("a.md", "Returns")], [], 5))
    assert math.isnan(reciprocal_rank([chunk("a.md", "Returns")], []))


def test_reciprocal_rank_uses_first_hit():
    ranked = [chunk("x.md", "Other"), chunk("x.md", "More"), chunk("a.md", "Returns")]
    assert reciprocal_rank(ranked, [A]) == pytest.approx(1 / 3)
    assert reciprocal_rank(ranked[:2], [A]) == 0.0


def test_percentile_and_mean():
    values = [1.0, 2.0, 3.0, 4.0, 100.0]
    assert percentile(values, 0.5) == 3.0
    assert percentile(values, 0.95) == 100.0
    assert math.isnan(percentile([], 0.5))
    assert mean([1.0, math.nan, 3.0]) == 2.0
    assert math.isnan(mean([]))
