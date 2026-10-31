from mabeyn.core.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    settings = Settings(_env_file=None)
    assert settings.log_level == "INFO"
    assert settings.database_url.startswith("postgresql+asyncpg://")


def test_env_override(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert Settings(_env_file=None).log_level == "DEBUG"


def test_secret_not_in_repr():
    settings = Settings(_env_file=None, openai_api_key="sk-test")
    assert "sk-test" not in repr(settings)
