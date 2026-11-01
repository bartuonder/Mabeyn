import pytest

from mabeyn.core.config import get_settings


@pytest.fixture(autouse=True)
def allow_shop_host(monkeypatch):
    monkeypatch.setenv("TOOL_ALLOWED_HOSTS", "shop")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("TOOL_ALLOWED_HOSTS")
    get_settings.cache_clear()
