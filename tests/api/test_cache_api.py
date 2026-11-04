from httpx import AsyncClient

from mabeyn.cache.answers import AnswerCache, MemoryKV
from mabeyn.db.models import AnswerCacheEntry
from mabeyn.db.session import tenant_session
from tests.api.test_api_keys import auth_headers
from tests.fakes import FakeLLM


async def rows(tenant_id) -> int:
    from sqlalchemy import func, select

    async with tenant_session(tenant_id) as session:
        return int(await session.scalar(select(func.count(AnswerCacheEntry.id))) or 0)


async def test_admins_can_clear_the_cache_of_their_tenant_only(
    client: AsyncClient, tenant_x, tenant_y, monkeypatch
):
    import mabeyn.cache.answers as module

    monkeypatch.setattr(module, "get_kv", lambda: MemoryKV())
    store = AnswerCache(FakeLLM(), MemoryKV())
    await store.store(tenant_x.id, "How many days to return?", "Thirty days.", [])
    await store.store(tenant_y.id, "How many days to return?", "Thirty days.", [])

    response = await client.delete("/cache", headers=await auth_headers(client, tenant_x))
    assert response.status_code == 204
    assert await rows(tenant_x.id) == 0 and await rows(tenant_y.id) == 1


async def test_clearing_the_cache_needs_an_admin(client: AsyncClient):
    assert (await client.delete("/cache")).status_code == 401
