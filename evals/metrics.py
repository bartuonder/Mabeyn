import math
from collections.abc import Sequence

from evals.datasets import Gold
from mabeyn.ingestion.chunk import PATH_SEPARATOR, SECTION_SEPARATOR
from mabeyn.rag.types import RetrievedChunk


def chunk_matches(chunk: RetrievedChunk, gold: Gold) -> bool:
    """A chunk hits a gold item when it comes from that document and covers that section."""
    if chunk.source_uri != gold.doc:
        return False
    return any(
        label.split(PATH_SEPARATOR)[-1].strip() == gold.section
        for label in chunk.section.split(SECTION_SEPARATOR)
    )


def recall_at_k(ranked: Sequence[RetrievedChunk], gold: Sequence[Gold], k: int) -> float:
    if not gold:
        return math.nan
    top = ranked[:k]
    found = sum(any(chunk_matches(c, g) for c in top) for g in gold)
    return found / len(gold)


def reciprocal_rank(ranked: Sequence[RetrievedChunk], gold: Sequence[Gold]) -> float:
    if not gold:
        return math.nan
    for position, chunk in enumerate(ranked, start=1):
        if any(chunk_matches(chunk, g) for g in gold):
            return 1.0 / position
    return 0.0


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return math.nan
    ordered = sorted(values)
    index = min(max(math.ceil(q * len(ordered)) - 1, 0), len(ordered) - 1)
    return ordered[index]


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a proportion; stays inside [0, 1] and behaves for small samples."""
    if total == 0:
        return math.nan, math.nan
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def mean(values: Sequence[float]) -> float:
    clean = [v for v in values if not math.isnan(v)]
    return sum(clean) / len(clean) if clean else math.nan
