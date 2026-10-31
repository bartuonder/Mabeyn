from fastapi import FastAPI

from apps.api.routers import health
from mabeyn.core.config import get_settings
from mabeyn.core.logging import configure_logging


def create_app() -> FastAPI:
    configure_logging(get_settings().log_level)
    app = FastAPI(title="Mabeyn", version="0.1.0")
    app.include_router(health.router)
    return app


app = create_app()
