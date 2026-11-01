import httpx
import pytest

from mabeyn.core.config import get_settings
from mabeyn.db.models import DocumentSource
from mabeyn.ingestion.fetch import FetchError, ensure_public, fetch_url


def resolver_for(mapping: dict[str, list[str]]):
    async def resolve(host: str) -> list[str]:
        return mapping[host]

    return resolve


PUBLIC = resolver_for({"example.com": ["93.184.216.34"], "cdn.example.com": ["93.184.216.35"]})


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "172.16.0.1"]
)
async def test_non_public_addresses_are_blocked(address: str):
    with pytest.raises(FetchError, match="non-public"):
        await ensure_public(
            "http://internal.example/", resolver_for({"internal.example": [address]})
        )


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "http:///nohost"])
async def test_non_http_schemes_are_blocked(url: str):
    with pytest.raises(FetchError, match="http"):
        await ensure_public(url, PUBLIC)


async def test_mixed_dns_answers_are_blocked_if_any_is_private():
    resolver = resolver_for({"mixed.example": ["93.184.216.34", "10.0.0.1"]})
    with pytest.raises(FetchError):
        await ensure_public("http://mixed.example/", resolver)


async def test_fetch_returns_html():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text="<h1>Hi</h1>", headers={"content-type": "text/html"}
        )
    )
    body, kind = await fetch_url("https://example.com/", resolver=PUBLIC, transport=transport)
    assert kind is DocumentSource.HTML and b"<h1>Hi</h1>" in body


async def test_fetch_detects_pdf_by_magic_bytes():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=b"%PDF-1.4 data", headers={"content-type": "application/octet-stream"}
        )
    )
    _, kind = await fetch_url("https://example.com/f", resolver=PUBLIC, transport=transport)
    assert kind is DocumentSource.PDF


async def test_redirect_to_private_host_is_blocked():
    resolver = resolver_for({"example.com": ["93.184.216.34"], "evil.example": ["127.0.0.1"]})
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "http://evil.example/secret"})
    )
    with pytest.raises(FetchError, match="non-public"):
        await fetch_url("https://example.com/", resolver=resolver, transport=transport)


async def test_redirect_to_public_host_is_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(301, headers={"location": "https://cdn.example.com/page"})
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    body, _ = await fetch_url(
        "https://example.com/", resolver=PUBLIC, transport=httpx.MockTransport(handler)
    )
    assert body == b"ok"


async def test_redirect_loop_is_stopped():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"location": "https://example.com/again"})
    )
    with pytest.raises(FetchError, match="redirects"):
        await fetch_url("https://example.com/", resolver=PUBLIC, transport=transport)


async def test_oversized_response_is_rejected(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "100")
    get_settings.cache_clear()
    try:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"x" * 500, headers={"content-type": "text/html"}
            )
        )
        with pytest.raises(FetchError, match="too large"):
            await fetch_url("https://example.com/", resolver=PUBLIC, transport=transport)
    finally:
        monkeypatch.delenv("MAX_UPLOAD_BYTES")
        get_settings.cache_clear()


async def test_error_status_and_unsupported_type_are_rejected():
    not_found = httpx.MockTransport(lambda request: httpx.Response(404))
    with pytest.raises(FetchError, match="status 404"):
        await fetch_url("https://example.com/", resolver=PUBLIC, transport=not_found)
    image = httpx.MockTransport(
        lambda request: httpx.Response(
            200, content=b"GIF89a", headers={"content-type": "image/gif"}
        )
    )
    with pytest.raises(FetchError, match="unsupported"):
        await fetch_url("https://example.com/", resolver=PUBLIC, transport=image)
