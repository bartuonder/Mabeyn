import uuid

import pytest

from mabeyn.db.session import tenant_session
from mabeyn.rag.hybrid import fts_search, hybrid_search, reciprocal_rank_fusion
from mabeyn.rag.retrieval import embed_query
from mabeyn.rag.types import RetrievedChunk
from mabeyn.seed import ingest_seed_documents, load_seed_documents
from tests.fakes import FakeLLM


@pytest.fixture
async def corpus(fresh_tenant):
    llm = FakeLLM()
    await ingest_seed_documents(fresh_tenant, load_seed_documents(), llm)
    return fresh_tenant, llm


async def test_turkish_stemming_matches_inflected_query_words(corpus):
    tenant_id, _ = corpus
    async with tenant_session(tenant_id) as session:
        hits = await fts_search(session, tenant_id, "KKTC'ye teslimat kaç günde yapılıyor", 5)
    assert hits and hits[0].source_uri == "yurt-disi-ve-kktc-gonderim.md"


async def test_english_documents_are_searched_with_the_english_config(corpus):
    tenant_id, _ = corpus
    async with tenant_session(tenant_id) as session:
        hits = await fts_search(session, tenant_id, "minimum purchase for tourist VAT refunds", 3)
    assert hits[0].source_uri == "tourist-vat-refund.md"


async def test_query_without_matches_returns_nothing(corpus):
    tenant_id, _ = corpus
    async with tenant_session(tenant_id) as session:
        assert await fts_search(session, tenant_id, "zzzqqq xxxyyy", 5) == []


async def test_fts_is_tenant_scoped(corpus, tenant_a):
    async with tenant_session(tenant_a.id) as session:
        assert await fts_search(session, tenant_a.id, "teslimat kargo iade", 5) == []


async def test_hybrid_surfaces_what_vector_search_alone_misses(corpus):
    tenant_id, llm = corpus
    query = "KKTC'ye teslimat kaç günde yapılıyor"
    vector = await embed_query(llm, query)
    async with tenant_session(tenant_id) as session:
        hits = await hybrid_search(session, tenant_id, query, vector, 5)
    assert "yurt-disi-ve-kktc-gonderim.md" in [h.source_uri for h in hits[:3]]
    assert len({h.chunk_id for h in hits}) == len(hits) <= 5


def make(label: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid5(uuid.NAMESPACE_DNS, label),
        document_id=uuid.uuid4(),
        document_title=label,
        source_uri=label,
        section="",
        page=None,
        content="",
        context_header="",
        effective_date=None,
        score=0.0,
    )


def test_rrf_rewards_agreement_between_rankings():
    a, b, c, d = (make(x) for x in "abcd")
    fused = reciprocal_rank_fusion([[a, b, c], [d, b, c]])
    assert [x.document_title for x in fused][:2] == ["b", "c"]
    assert fused[0].score > fused[-1].score


def test_rrf_with_one_empty_ranking_keeps_the_other_order():
    a, b = make("a"), make("b")
    assert [x.document_title for x in reciprocal_rank_fusion([[a, b], []])] == ["a", "b"]
    assert reciprocal_rank_fusion([[], []]) == []
