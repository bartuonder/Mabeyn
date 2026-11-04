import json
import uuid

import pytest
from sqlalchemy import func, select

from mabeyn.cache.answers import (
    AnswerCache,
    MemoryKV,
    cacheable_answer,
    cacheable_question,
    invalidate_answers,
    normalise,
)
from mabeyn.core.config import get_settings
from mabeyn.db.models import AnswerCacheEntry
from mabeyn.db.session import tenant_session
from tests.fakes import FakeLLM

CITATIONS = [{"cite": "Return Policy:Eligibility", "document_title": "Return Policy"}]
QUESTION = "How many days do I have to return a product?"
ANSWER = "You can return a product within 30 days. [Return Policy:Eligibility]"


def cache(confirm: bool = True) -> tuple[AnswerCache, MemoryKV, FakeLLM]:
    llm, kv = FakeLLM(lambda messages, model: json.dumps({"same": confirm})), MemoryKV()
    return AnswerCache(llm, kv), kv, llm


async def rows(tenant_id: uuid.UUID) -> int:
    async with tenant_session(tenant_id) as session:
        return int(await session.scalar(select(func.count(AnswerCacheEntry.id))) or 0)


def test_normalising_ignores_case_punctuation_and_diacritics():
    assert normalise("  İade   NASIL yapılır?! ") == normalise("iade nasil yapilir")


@pytest.mark.parametrize(
    "question",
    ["Where is order KS-1001?", "Call me on 0532 123 45 67", "x" * 400, "", "mail me at a@b.com"],
)
def test_questions_about_one_customer_are_not_cacheable(question):
    assert not cacheable_question(question)


def test_ordinary_questions_and_answers_are_cacheable():
    assert cacheable_question(QUESTION)
    assert cacheable_answer(ANSWER)
    assert not cacheable_answer("Refund to TR33 0006 1005 1978 6457 8413 26.")
    assert not cacheable_answer("   ")


async def test_a_stored_answer_comes_back_for_the_same_question(fresh_tenant):
    store, kv, llm = cache()
    assert await store.lookup(fresh_tenant, QUESTION) is None
    assert await store.store(fresh_tenant, QUESTION, ANSWER, CITATIONS)

    again = await store.lookup(fresh_tenant, "HOW many days do I have to return a product??")
    assert again is not None and again.layer == "exact" and again.similarity == 1.0
    assert again.answer == ANSWER and again.citations == CITATIONS
    assert await rows(fresh_tenant) == 1


async def test_a_close_paraphrase_hits_the_semantic_layer_and_a_different_question_does_not(
    fresh_tenant,
):
    store, kv, llm = cache()
    await store.store(fresh_tenant, QUESTION, ANSWER, CITATIONS)
    kv.data.clear()  # only the semantic layer is left

    same = await store.lookup(fresh_tenant, QUESTION)
    assert same is not None and same.layer == "semantic" and same.similarity > 0.99
    near = await store.lookup(fresh_tenant, "How many days do I have to return a product please")
    assert near is not None and near.layer == "semantic" and 0.6 <= near.similarity < 1.0
    assert await store.lookup(fresh_tenant, "What payment methods do you accept") is None


async def test_a_similar_question_is_served_only_when_the_model_confirms_it(fresh_tenant):
    store, kv, llm = cache(confirm=False)
    await store.store(fresh_tenant, QUESTION, ANSWER, CITATIONS)
    kv.data.clear()
    similar = "How many days do I have to return a product please"
    assert await store.lookup(fresh_tenant, similar) is None
    asked = llm.chat_calls[-1][1][1]["content"]
    assert QUESTION in asked and similar in asked


async def test_the_candidate_threshold_decides_what_the_model_is_asked_about(
    fresh_tenant, monkeypatch
):
    store, kv, llm = cache()
    await store.store(fresh_tenant, QUESTION, ANSWER, CITATIONS)
    kv.data.clear()
    monkeypatch.setenv("CACHE_SEMANTIC_THRESHOLD", "0.999")
    get_settings.cache_clear()
    try:
        similar = "How many days do I have to return a product please"
        assert await store.lookup(fresh_tenant, similar) is None
        assert llm.chat_calls == []
    finally:
        get_settings.cache_clear()


async def test_tenants_never_see_each_others_answers(tenant_x, tenant_y):
    store, kv, llm = cache()
    await store.store(tenant_x.id, QUESTION, ANSWER, CITATIONS)
    assert await store.lookup(tenant_y.id, QUESTION) is None
    kv.data.clear()
    assert await store.lookup(tenant_y.id, QUESTION) is None
    assert await rows(tenant_y.id) == 0


async def test_invalidation_removes_exact_and_semantic_entries(fresh_tenant):
    store, kv, llm = cache()
    await store.store(fresh_tenant, QUESTION, ANSWER, CITATIONS)
    await store.invalidate(fresh_tenant)
    assert await store.lookup(fresh_tenant, QUESTION) is None
    assert await rows(fresh_tenant) == 0
    await store.store(fresh_tenant, QUESTION, "Updated answer.", CITATIONS)
    hit = await store.lookup(fresh_tenant, QUESTION)
    assert hit is not None and hit.answer == "Updated answer."


async def test_a_new_prompt_version_makes_old_entries_unreachable(fresh_tenant, monkeypatch):
    import mabeyn.cache.answers as module

    store, kv, llm = cache()
    await store.store(fresh_tenant, QUESTION, ANSWER, CITATIONS)
    monkeypatch.setattr(module, "prompt_hash", lambda: "another-prompt-version")
    assert await store.lookup(fresh_tenant, QUESTION) is None


async def test_expired_entries_are_ignored(fresh_tenant, monkeypatch):
    store, kv, llm = cache()
    await store.store(fresh_tenant, QUESTION, ANSWER, CITATIONS)
    kv.data.clear()
    async with tenant_session(fresh_tenant) as session:
        from sqlalchemy import text

        await session.execute(
            text("UPDATE answer_cache SET expires_at = now() - interval '1 minute'")
        )
    assert await store.lookup(fresh_tenant, QUESTION) is None


async def test_the_cache_keeps_only_the_newest_entries(fresh_tenant, monkeypatch):
    monkeypatch.setenv("CACHE_MAX_ENTRIES", "3")
    get_settings.cache_clear()
    try:
        store, kv, llm = cache()
        for index in range(5):
            await store.store(fresh_tenant, f"Question number {index} about delivery", ANSWER, [])
        assert await rows(fresh_tenant) == 3
    finally:
        get_settings.cache_clear()


async def test_unsafe_content_is_not_stored(fresh_tenant):
    store, kv, llm = cache()
    assert not await store.store(fresh_tenant, "Where is KS-1001?", ANSWER, [])
    assert not await store.store(fresh_tenant, QUESTION, "Call 0532 123 45 67 now.", [])
    assert await rows(fresh_tenant) == 0


async def test_invalidating_one_tenant_leaves_the_others(tenant_x, tenant_y):
    store, kv, llm = cache()
    await store.store(tenant_x.id, QUESTION, ANSWER, CITATIONS)
    await store.store(tenant_y.id, QUESTION, ANSWER, CITATIONS)
    await invalidate_answers(tenant_x.id, kv)
    assert await store.lookup(tenant_x.id, QUESTION) is None
    assert await store.lookup(tenant_y.id, QUESTION) is not None


async def test_contact_details_the_tenant_publishes_do_not_block_caching(fresh_tenant):
    from mabeyn.db import models as m
    from mabeyn.ingestion.pipeline import add_document, ingest_document

    page = b"# Support\n\nWrite to destek@kargoshop.example or call 0850 123 45 67.\n"
    async with tenant_session(fresh_tenant) as session:
        document = await add_document(
            session,
            tenant_id=fresh_tenant,
            title="Support",
            source_type=m.DocumentSource.MARKDOWN,
            data=page,
        )
        document_id = document.id
    await ingest_document(fresh_tenant, document_id, llm=FakeLLM())

    store, kv, llm = cache()
    published = "Write to destek@kargoshop.example or call 0850 123 45 67."
    assert await store.store(fresh_tenant, "How can I contact customer service?", published, [])
    other = "Write to someone.else@example.com instead."
    assert not await store.store(fresh_tenant, "Who can I write to about a complaint?", other, [])


async def test_a_redis_outage_does_not_stop_the_table_from_being_emptied(fresh_tenant):
    class DownKV(MemoryKV):
        async def incr(self, key: str) -> int:
            raise ConnectionError("redis is down")

    store, kv, llm = cache()
    await store.store(fresh_tenant, QUESTION, ANSWER, CITATIONS)
    await invalidate_answers(fresh_tenant, DownKV())
    assert await rows(fresh_tenant) == 0
