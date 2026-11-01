import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlparse

import httpx

from mabeyn.core.config import get_settings
from mabeyn.db.models import DocumentSource

MAX_REDIRECTS = 3
ALLOWED_SCHEMES = {"http", "https"}
Resolver = Callable[[str], Awaitable[list[str]]]


class FetchError(Exception):
    pass


async def resolve_host(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise FetchError(f"cannot resolve host: {host}") from exc
    return [str(info[4][0]) for info in infos]


async def ensure_public(url: str, resolver: Resolver) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
        raise FetchError("only http and https URLs are allowed")
    for address in await resolver(parsed.hostname):
        if not ipaddress.ip_address(address.split("%")[0]).is_global:
            raise FetchError("URL resolves to a non-public address")


async def fetch_url(
    url: str,
    *,
    resolver: Resolver = resolve_host,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, DocumentSource]:
    """Fetch a public page. DNS rebinding is not covered; run workers without internal access."""
    settings = get_settings()
    limit = settings.max_upload_bytes
    async with httpx.AsyncClient(
        timeout=settings.url_fetch_timeout_seconds, follow_redirects=False, transport=transport
    ) as client:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await ensure_public(current, resolver)
            try:
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("redirect without location")
                        current = urljoin(current, location)
                        continue
                    if response.status_code != 200:
                        raise FetchError(f"unexpected status {response.status_code}")
                    body = bytearray()
                    async for piece in response.aiter_bytes():
                        body.extend(piece)
                        if len(body) > limit:
                            raise FetchError("response is too large")
                    content_type = response.headers.get("content-type", "").lower()
            except httpx.HTTPError as exc:
                raise FetchError(f"request failed: {exc.__class__.__name__}") from exc
            if "pdf" in content_type or bytes(body[:5]) == b"%PDF-":
                return bytes(body), DocumentSource.PDF
            if "html" in content_type or "text/plain" in content_type:
                return bytes(body), DocumentSource.HTML
            raise FetchError("unsupported content type")
    raise FetchError("too many redirects")
