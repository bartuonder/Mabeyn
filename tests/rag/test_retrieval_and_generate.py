import uuid
from datetime import date

from mabeyn.db import models as m
from mabeyn.db.session import tenant_session
from mabeyn.ingestion.pipeline import add_document, ingest_document
from mabeyn.rag import prompts
from mabeyn.rag.generate import (
    build_messages,
    extract_citations,
    format_sources,
    generate_answer,
    safe_fallback,
    strip_unknown_tags,
)
from mabeyn.rag.retrieval import retrieve_vector
from mabeyn.rag.types import RetrievedChunk
from tests.fakes import FakeLLM

DOCS = {
    "Return Policy": (
        b"# Return Policy\n\n## Eligibility\n\n"
        b"Items can be returned within thirty days of delivery with the original receipt."
    ),
    "Shipping FAQ": (
        b"# Shipping FAQ\n\n## Delivery time\n\n"
        b"Standard shipping takes three to five business days across the country."
    ),
    "Warranty": (
        b"# Warranty\n\n## Coverage\n\n"
        b"Electronics carry a two year manufacturer warranty against defects."
    ),
}


async def seed(tenant_id: uuid.UUID, llm: FakeLLM, docs=DOCS) -> dict[str, uuid.UUID]:
    ids = {}
    for title, data in docs.items():
        async with tenant_session(tenant_id) as s:
            document = await add_document(
                s,
                tenant_id=tenant_id,
                title=title,
                source_type=m.DocumentSource.MARKDOWN,
                data=data,
            )
            ids[title] = document.id
        await ingest_document(tenant_id, ids[title], llm=llm)
    return ids


async def test_vector_search_ranks_the_matching_document_first(fresh_tenant):
    llm = FakeLLM()
    await seed(fresh_tenant, llm)
    results = await retrieve_vector(fresh_tenant, llm, "how many days to return items", k=3)
    assert results[0].document_title == "Return Policy"
    assert results[0].score > results[-1].score
    assert len(results) == 3


async def test_search_respects_k_and_ready_status(fresh_tenant):
    llm = FakeLLM()
    ids = await seed(fresh_tenant, llm)
    async with tenant_session(fresh_tenant) as s:
        document = await s.get(m.Document, ids["Return Policy"])
        document.status = m.DocumentStatus.FAILED
    results = await retrieve_vector(fresh_tenant, llm, "return items thirty days", k=5)
    assert "Return Policy" not in {r.document_title for r in results}
    assert len(await retrieve_vector(fresh_tenant, llm, "warranty", k=1)) == 1


async def test_search_never_returns_other_tenants_chunks(fresh_tenant, tenant_a, admin_engine):
    llm = FakeLLM()
    await seed(fresh_tenant, llm)
    results = await retrieve_vector(tenant_a.id, llm, "return items thirty days", k=5)
    assert results == []


def make_chunk(
    title="Return Policy", section="Return Policy > Eligibility", **kw
) -> RetrievedChunk:
    defaults = dict(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        source_uri=None,
        page=None,
        content="Thirty days.",
        context_header=title,
        effective_date=None,
        score=0.9,
    )
    return RetrievedChunk(document_title=title, section=section, **{**defaults, **kw})


def test_cite_tag_uses_document_and_last_heading():
    chunk = make_chunk(
        title="Return: Policy [v2]", section="Return > Eligibility | Return > Refund"
    )
    assert chunk.cite == "Return  Policy  v2:Eligibility"


def test_prompt_marks_sources_as_data_and_includes_dates():
    chunk = make_chunk(effective_date=date(2025, 1, 1))
    messages = build_messages("KargoShop", "Can I return it?", [chunk])
    assert messages[0]["role"] == "system" and "KargoShop" in messages[0]["content"]
    assert "untrusted" in messages[0]["content"]
    assert (
        '<source document="Return Policy" effective_date="2025-01-01" '
        'tags="[Return Policy:Eligibility]">' in messages[-1]["content"]
    )
    assert prompts.PROMPT_VERSION


def test_merged_chunk_offers_a_tag_per_section_and_resolves_each():
    merged = make_chunk(
        title="Return Policy", section="Return Policy > Period | Return Policy > Shipping Cost"
    )
    assert merged.cites == ["Return Policy:Period", "Return Policy:Shipping Cost"]
    assert "[Return Policy:Period] [Return Policy:Shipping Cost]" in format_sources([merged])

    answer = "Free shipping [Return Policy:Shipping Cost]."
    (citation,) = extract_citations(answer, [merged])
    assert citation.cite == "Return Policy:Shipping Cost"
    assert citation.chunk_ids == (merged.chunk_id,)


def test_history_is_trimmed_to_recent_messages():
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(20)
    ]
    messages = build_messages("Shop", "q", [], history)
    assert len(messages) == 1 + 6 + 1
    assert messages[1]["content"] == "m14"


def test_citations_resolve_known_tags_only():
    chunk = make_chunk()
    answer = (
        "Thirty days [Return Policy:Eligibility]. "
        "Also true [Made Up:Tag] [Return Policy:Eligibility]."
    )
    citations = extract_citations(answer, [chunk, make_chunk()])
    assert [c.cite for c in citations] == ["Return Policy:Eligibility"]
    assert len(citations[0].chunk_ids) == 2
    assert "[Made Up:Tag]" not in strip_unknown_tags(answer, [chunk])
    assert "[Return Policy:Eligibility]" in strip_unknown_tags(answer, [chunk])


async def test_generate_answer_streams_through_llm_and_uses_sources():
    seen = {}

    def handler(messages, model):
        seen["messages"] = messages
        seen["model"] = model
        return "Within thirty days [Return Policy:Eligibility]."

    llm = FakeLLM(handler)
    answer = await generate_answer(llm, "Shop", "Return window?", [make_chunk()])
    assert answer == "Within thirty days [Return Policy:Eligibility]."
    assert "Thirty days." in seen["messages"][-1]["content"]


async def test_fallback_uses_the_small_model():
    llm = FakeLLM(lambda messages, model: "Bilgi bulunamadı.")
    assert await safe_fallback(llm, "Ürün garantisi?") == "Bilgi bulunamadı."
    assert llm.chat_calls[0][0] == "gpt-4o-mini"


async def test_baseline_without_generation_only_retrieves(fresh_tenant):
    from mabeyn.rag.service import BaselineRag, collect

    llm = FakeLLM()
    await seed(fresh_tenant, llm)
    final = await collect(
        BaselineRag(llm, 2, generate=False).run(fresh_tenant, "return", [], "Shop")
    )
    assert final.answer == "" and len(final.ranked) == 2
    assert llm.chat_calls == []
