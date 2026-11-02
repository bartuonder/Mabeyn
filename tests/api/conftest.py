from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from apps.api.main import app
from mabeyn.agents.runtime import get_memory_service
from mabeyn.memory.service import MemoryService
from tests.fakes import FakeLLM


@pytest_asyncio.fixture(autouse=True)
async def memory_service(memory_store) -> AsyncIterator[MemoryService]:
    service = MemoryService(FakeLLM(lambda messages, model: '{"memories": []}'), memory_store)
    app.dependency_overrides[get_memory_service] = lambda: service
    yield service
    app.dependency_overrides.pop(get_memory_service, None)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
