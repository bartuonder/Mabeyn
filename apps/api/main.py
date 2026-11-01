from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.routers import (
    api_keys,
    auth,
    chat,
    conversations,
    documents,
    health,
    sessions,
    tools,
)
from mabeyn.core.config import get_settings
from mabeyn.core.logging import configure_logging
from mabeyn.core.tracking import export_tracking_env
from mabeyn.db.session import get_engine
from mabeyn.queue import get_ingestion_queue


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await get_ingestion_queue().close()
    await get_engine().dispose()


def create_app() -> FastAPI:
    export_tracking_env()
    configure_logging(get_settings().log_level)
    app = FastAPI(title="Mabeyn", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(api_keys.router)
    app.include_router(sessions.router)
    app.include_router(documents.router)
    app.include_router(conversations.router)
    app.include_router(tools.router)
    app.include_router(chat.router)
    return app


app = create_app()
