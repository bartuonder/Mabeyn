import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from mabeyn.db.models import Document, DocumentSource, RiskLevel, Tenant, ToolDefinition
from mabeyn.db.provisioning import create_tenant
from mabeyn.db.session import tenant_session
from mabeyn.ingestion.chunk import ChunkConfig
from mabeyn.ingestion.parse import parse_markdown
from mabeyn.ingestion.pipeline import add_document, ingest_document
from mabeyn.llm.client import LLM

SEED_ROOT = Path(__file__).resolve().parents[1] / "seed" / "kargoshop" / "docs"
TOOLS_FILE = Path(__file__).resolve().parents[1] / "seed" / "kargoshop" / "tools.json"
DEFAULT_SHOP_URL = "http://localhost:9000"
TENANT_SLUG = "kargoshop"
ADMIN_EMAIL = "admin@kargoshop.example"
INGEST_CONCURRENCY = 4


@dataclass(frozen=True)
class SeedDocument:
    filename: str
    title: str
    language: str
    effective_date: date | None
    data: bytes


def load_seed_documents(
    root: Path = SEED_ROOT, languages: tuple[str, ...] = ("tr", "en")
) -> list[SeedDocument]:
    documents = []
    for language in languages:
        for path in sorted((root / language).glob("*.md")):
            data = path.read_bytes()
            parsed = parse_markdown(data.decode("utf-8"))
            raw_date = parsed.metadata.get("effective_date")
            documents.append(
                SeedDocument(
                    filename=path.name,
                    title=parsed.title or path.stem,
                    language=parsed.metadata.get("language", language),
                    effective_date=date.fromisoformat(raw_date) if raw_date else None,
                    data=data,
                )
            )
    return documents


async def ingest_seed_documents(
    tenant_id: uuid.UUID,
    documents: list[SeedDocument],
    llm: LLM,
    chunk_config: ChunkConfig | None = None,
    embedding_model: str | None = None,
) -> None:
    gate = asyncio.Semaphore(INGEST_CONCURRENCY)

    async def ingest(seed: SeedDocument) -> None:
        async with gate:
            async with tenant_session(tenant_id) as session:
                document = await add_document(
                    session,
                    tenant_id=tenant_id,
                    title=seed.title,
                    source_type=DocumentSource.MARKDOWN,
                    data=seed.data,
                    source_uri=seed.filename,
                    language=seed.language,
                    effective_date=seed.effective_date,
                )
                document_id = document.id
            await ingest_document(
                tenant_id,
                document_id,
                llm=llm,
                chunk_config=chunk_config,
                embedding_model=embedding_model,
            )

    await asyncio.gather(*(ingest(d) for d in documents))


def load_tool_definitions(shop_url: str = DEFAULT_SHOP_URL) -> list[dict[str, object]]:
    definitions = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    for definition in definitions:
        definition["url_template"] = shop_url.rstrip("/") + definition.pop("path")
    return definitions  # type: ignore[no-any-return]


async def register_kargoshop_tools(tenant_id: uuid.UUID, shop_url: str = DEFAULT_SHOP_URL) -> int:
    """Create or update the demo tenant's tools; returns how many exist afterwards."""
    definitions = load_tool_definitions(shop_url)
    async with tenant_session(tenant_id) as session:
        existing = {t.name: t for t in await session.scalars(select(ToolDefinition))}
        for definition in definitions:
            tool = existing.get(str(definition["name"]))
            if tool is None:
                tool = ToolDefinition(tenant_id=tenant_id)
                session.add(tool)
            tool.name = str(definition["name"])
            tool.description = str(definition["description"])
            tool.json_schema = definition["json_schema"]  # type: ignore[assignment]
            tool.risk_level = RiskLevel(str(definition["risk_level"]))
            tool.http_method = str(definition["http_method"])
            tool.url_template = str(definition["url_template"])
    return len(definitions)


async def seed_kargoshop(
    engine: AsyncEngine, llm: LLM, admin_password: str, shop_url: str = DEFAULT_SHOP_URL
) -> uuid.UUID:
    """Create the demo tenant (if missing), its tools and any seed documents it lacks."""
    async with AsyncSession(engine) as session:
        tenant_id = await session.scalar(select(Tenant.id).where(Tenant.slug == TENANT_SLUG))
    if tenant_id is None:
        tenant_id = await create_tenant(
            engine, "KargoShop", TENANT_SLUG, ADMIN_EMAIL, admin_password
        )

    async with tenant_session(tenant_id) as session:
        existing = set(await session.scalars(select(Document.source_uri)))
    missing = [d for d in load_seed_documents() if d.filename not in existing]
    await ingest_seed_documents(tenant_id, missing, llm)
    await register_kargoshop_tools(tenant_id, shop_url)
    return tenant_id
