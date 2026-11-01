import re
from datetime import date

from sqlalchemy import select

from mabeyn.db.models import Chunk, Document, DocumentStatus
from mabeyn.db.session import tenant_session
from mabeyn.ingestion.chunk import chunk_document
from mabeyn.ingestion.parse import parse_markdown
from mabeyn.seed import ingest_seed_documents, load_seed_documents
from tests.fakes import FakeLLM


def test_seed_corpus_shape():
    documents = load_seed_documents()
    names = [d.filename for d in documents]
    assert len(names) == len(set(names))
    assert sum(d.language == "tr" for d in documents) >= 25
    assert sum(d.language == "en" for d in documents) >= 5
    assert all(d.effective_date is not None for d in documents)
    assert all(re.fullmatch(r"[a-z0-9-]+\.md", n) for n in names)


def test_contradicting_policies_exist_with_different_dates():
    by_name = {d.filename: d for d in load_seed_documents()}
    old, new = by_name["iade-politikasi-2023.md"], by_name["iade-politikasi.md"]
    assert old.effective_date == date(2023, 3, 1)
    assert new.effective_date == date(2025, 1, 15)
    assert b"14 gun" in old.data.replace("ü".encode(), b"u")
    assert b"30 g" in new.data


def test_every_document_has_sections_and_chunks():
    for seed in load_seed_documents():
        parsed = parse_markdown(seed.data.decode("utf-8"))
        assert len(parsed.sections) >= 2, seed.filename
        assert chunk_document(parsed, seed.title), seed.filename


async def test_seed_documents_ingest_into_a_tenant(fresh_tenant):
    documents = load_seed_documents()
    await ingest_seed_documents(fresh_tenant, documents, FakeLLM())
    async with tenant_session(fresh_tenant) as s:
        statuses = (await s.scalars(select(Document.status))).all()
        chunk_count = len((await s.scalars(select(Chunk.id))).all())
    assert len(statuses) == len(documents)
    assert set(statuses) == {DocumentStatus.READY}
    assert chunk_count >= len(documents)


def test_seed_tool_definitions_are_valid_and_cover_the_planned_tools():
    from mabeyn.seed import load_tool_definitions
    from mabeyn.tools.service import validate_definition

    definitions = load_tool_definitions("http://shop")
    assert {d["name"] for d in definitions} == {
        "get_order_status",
        "list_orders",
        "create_ticket",
        "request_refund",
        "change_address",
    }
    for d in definitions:
        validate_definition(
            str(d["name"]), d["json_schema"], str(d["http_method"]), str(d["url_template"])
        )  # type: ignore[arg-type]
    risks = {d["name"]: d["risk_level"] for d in definitions}
    assert risks["request_refund"] == "sensitive" and risks["get_order_status"] == "read"


async def test_tools_register_idempotently(fresh_tenant):
    from mabeyn.seed import register_kargoshop_tools
    from mabeyn.tools.service import load_tools

    assert await register_kargoshop_tools(fresh_tenant, "http://shop") == 5
    assert await register_kargoshop_tools(fresh_tenant, "http://shop:9000") == 5
    tools = await load_tools(fresh_tenant)
    assert len(tools) == 5
    assert all(t.url_template.startswith("http://shop:9000/") for t in tools)
