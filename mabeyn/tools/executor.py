import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from mabeyn.core.config import get_settings
from mabeyn.ingestion.fetch import FetchError, Resolver, ensure_public, resolve_host

PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
RETRYABLE_METHODS = {"GET"}
BODY_METHODS = {"POST", "PUT", "PATCH"}
MODEL_RESULT_CHARS = 4000


@dataclass(frozen=True)
class ToolContext:
    """Verified identity of the caller; injected by the platform, never by the model."""

    tenant_id: uuid.UUID
    customer_id: str


@dataclass(frozen=True)
class ToolEndpoint:
    method: str
    url_template: str


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any
    error: str | None = None
    status_code: int | None = None
    duration_ms: int = 0

    def for_model(self, limit: int = MODEL_RESULT_CHARS) -> str:
        payload = {"ok": self.ok, "status": self.status_code, "data": self.data}
        if self.error:
            payload["error"] = self.error
        text = json.dumps(payload, ensure_ascii=False, default=str)
        return text if len(text) <= limit else text[:limit] + '..."truncated"'


class ToolExecutionError(Exception):
    pass


def split_arguments(template: str, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Separate path parameters from the remaining arguments."""
    names = PLACEHOLDER.findall(template)
    missing = [n for n in names if n not in args]
    if missing:
        raise ToolExecutionError(f"missing path arguments: {missing}")
    path = {n: args[n] for n in names}
    rest = {k: v for k, v in args.items() if k not in path}
    return path, rest


def build_url(template: str, path_args: dict[str, Any]) -> str:
    return PLACEHOLDER.sub(lambda m: quote(str(path_args[m.group(1)]), safe=""), template)


class HttpToolExecutor:
    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver = resolve_host,
        max_retries: int = 2,
        backoff_seconds: float = 0.2,
    ) -> None:
        self.transport = transport
        self.resolver = resolver
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    async def _check_host(self, url: str) -> None:
        settings = get_settings()
        allowed = {h.strip().lower() for h in settings.tool_allowed_hosts.split(",") if h.strip()}
        host = (urlparse(url).hostname or "").lower()
        if host in allowed:
            return
        try:
            await ensure_public(url, self.resolver)
        except FetchError as exc:
            raise ToolExecutionError(str(exc)) from exc

    async def execute(
        self,
        endpoint: ToolEndpoint,
        args: dict[str, Any],
        context: ToolContext,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        started = time.perf_counter()
        method = endpoint.method.upper()
        try:
            path_args, rest = split_arguments(endpoint.url_template, args)
            url = build_url(endpoint.url_template, path_args)
            await self._check_host(url)
            payload = {**rest, "customer_id": context.customer_id}
            result = await self._send(method, url, payload, context, idempotency_key)
        except ToolExecutionError as exc:
            result = ToolResult(ok=False, data=None, error=str(exc))
        elapsed = int((time.perf_counter() - started) * 1000)
        return ToolResult(result.ok, result.data, result.error, result.status_code, elapsed)

    async def _send(
        self,
        method: str,
        url: str,
        payload: dict[str, Any],
        context: ToolContext,
        idempotency_key: str | None,
    ) -> ToolResult:
        settings = get_settings()
        headers = {"X-Tenant-Id": str(context.tenant_id)}
        if idempotency_key and method not in RETRYABLE_METHODS:
            # The same call always carries the same key, so a replay cannot act twice.
            headers["Idempotency-Key"] = idempotency_key
        request_args: dict[str, Any] = (
            {"json": payload} if method in BODY_METHODS else {"params": payload}
        )
        attempts = 1 + (self.max_retries if method in RETRYABLE_METHODS else 0)
        last_error = "request failed"
        async with httpx.AsyncClient(
            timeout=settings.tool_timeout_seconds, follow_redirects=False, transport=self.transport
        ) as client:
            for attempt in range(attempts):
                try:
                    return await self._once(client, method, url, headers, request_args)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = f"{exc.__class__.__name__} while calling the tool"
                except _RetryableStatusError as exc:
                    last_error = f"upstream returned {exc.status_code}"
                    if attempt == attempts - 1:
                        return ToolResult(False, exc.data, last_error, exc.status_code)
                if attempt < attempts - 1:
                    await asyncio.sleep(self.backoff_seconds * (2**attempt))
        return ToolResult(ok=False, data=None, error=last_error)

    async def _once(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        headers: dict[str, str],
        request_args: dict[str, Any],
    ) -> ToolResult:
        limit = get_settings().tool_max_response_bytes
        async with client.stream(method, url, headers=headers, **request_args) as response:
            body = bytearray()
            async for piece in response.aiter_bytes():
                body.extend(piece)
                if len(body) > limit:
                    return ToolResult(False, None, "response is too large", response.status_code)
            status = response.status_code
        data = _parse_body(bytes(body))
        if 300 <= status < 400:
            return ToolResult(False, data, "redirects are not followed", status)
        if status >= 500 and method in RETRYABLE_METHODS:
            raise _RetryableStatusError(status, data)
        if status >= 400:
            return ToolResult(False, data, f"http {status}", status)
        return ToolResult(True, data, None, status)


class _RetryableStatusError(Exception):
    def __init__(self, status_code: int, data: Any) -> None:
        super().__init__(status_code)
        self.status_code = status_code
        self.data = data


def _parse_body(body: bytes) -> Any:
    text = body.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text[:MODEL_RESULT_CHARS]
