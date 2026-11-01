import json
import uuid

import httpx
import pytest
from httpx import ASGITransport

from apps.kargoshop_mock.main import Store, create_app
from mabeyn.core.config import get_settings
from mabeyn.tools.executor import HttpToolExecutor, ToolContext, ToolEndpoint

CONTEXT = ToolContext(tenant_id=uuid.uuid4(), customer_id="customer-1")


@pytest.fixture
def allow_shop(monkeypatch):
    monkeypatch.setenv("TOOL_ALLOWED_HOSTS", "shop,slow")
    get_settings.cache_clear()
    yield
    monkeypatch.delenv("TOOL_ALLOWED_HOSTS")
    get_settings.cache_clear()


def executor(handler, **kwargs) -> HttpToolExecutor:
    return HttpToolExecutor(transport=httpx.MockTransport(handler), backoff_seconds=0.0, **kwargs)


def json_response(data, status=200) -> httpx.Response:
    return httpx.Response(status, json=data)


async def test_get_sends_context_as_query_and_header(allow_shop):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["tenant"] = request.headers["x-tenant-id"]
        return json_response({"id": "KS-1"})

    result = await executor(handler).execute(
        ToolEndpoint("GET", "http://shop/orders/{order_id}"), {"order_id": "KS 1/2"}, CONTEXT
    )
    assert result.ok and result.data == {"id": "KS-1"}
    assert seen["url"] == "http://shop/orders/KS%201%2F2?customer_id=customer-1"
    assert seen["tenant"] == str(CONTEXT.tenant_id)


async def test_post_sends_json_body_and_model_cannot_override_identity(allow_shop):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return json_response({"id": "T-1"}, 201)

    result = await executor(handler).execute(
        ToolEndpoint("POST", "http://shop/tickets"),
        {"subject": "Late", "customer_id": "customer-2"},
        CONTEXT,
    )
    assert result.ok and result.status_code == 201
    assert seen["body"] == {"subject": "Late", "customer_id": "customer-1"}


async def test_missing_path_argument_fails_without_a_request(allow_shop):
    calls = []
    result = await executor(lambda r: calls.append(r)).execute(
        ToolEndpoint("GET", "http://shop/orders/{order_id}"), {}, CONTEXT
    )
    assert not result.ok and "missing path arguments" in (result.error or "")
    assert calls == []


async def test_private_hosts_are_blocked_unless_allowed(monkeypatch):
    async def private(host: str) -> list[str]:
        return ["10.0.0.5"]

    calls = []
    blocked = await executor(
        lambda r: calls.append(r) or json_response({}), resolver=private
    ).execute(ToolEndpoint("GET", "http://internal.example/x"), {}, CONTEXT)
    assert not blocked.ok and "non-public" in (blocked.error or "") and calls == []


async def test_get_retries_server_errors_then_succeeds(allow_shop):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return (
            json_response({"error": "busy"}, 503) if len(attempts) < 3 else json_response({"ok": 1})
        )

    result = await executor(handler).execute(ToolEndpoint("GET", "http://shop/x"), {}, CONTEXT)
    assert result.ok and len(attempts) == 3


async def test_get_gives_up_after_the_retry_budget(allow_shop):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return json_response({"error": "down"}, 502)

    result = await executor(handler, max_retries=1).execute(
        ToolEndpoint("GET", "http://shop/x"), {}, CONTEXT
    )
    assert not result.ok and result.status_code == 502 and len(attempts) == 2


async def test_writes_are_never_retried(allow_shop):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return json_response({"error": "down"}, 503)

    result = await executor(handler).execute(
        ToolEndpoint("POST", "http://shop/refund"), {}, CONTEXT
    )
    assert not result.ok and result.status_code == 503 and len(attempts) == 1


async def test_timeouts_are_retried_for_reads_and_reported(allow_shop):
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    result = await executor(handler, max_retries=2).execute(
        ToolEndpoint("GET", "http://slow/x"), {}, CONTEXT
    )
    assert not result.ok and "ReadTimeout" in (result.error or "") and len(attempts) == 3


async def test_oversized_responses_are_rejected(allow_shop, monkeypatch):
    monkeypatch.setenv("TOOL_MAX_RESPONSE_BYTES", "100")
    get_settings.cache_clear()
    result = await executor(lambda r: httpx.Response(200, content=b"x" * 500)).execute(
        ToolEndpoint("GET", "http://shop/x"), {}, CONTEXT
    )
    assert not result.ok and result.error == "response is too large"


async def test_client_errors_return_the_backend_message(allow_shop):
    result = await executor(lambda r: json_response({"detail": "Order not found"}, 404)).execute(
        ToolEndpoint("GET", "http://shop/x"), {}, CONTEXT
    )
    assert (
        not result.ok and result.status_code == 404 and result.data == {"detail": "Order not found"}
    )


async def test_redirects_are_not_followed(allow_shop):
    result = await executor(
        lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    ).execute(ToolEndpoint("GET", "http://shop/x"), {}, CONTEXT)
    assert not result.ok and result.error == "redirects are not followed"


async def test_non_json_bodies_come_back_as_text(allow_shop):
    result = await executor(lambda r: httpx.Response(200, text="plain words")).execute(
        ToolEndpoint("GET", "http://shop/x"), {}, CONTEXT
    )
    assert result.ok and result.data == "plain words"


def test_model_view_is_truncated():
    from mabeyn.tools.executor import ToolResult

    text = ToolResult(True, {"blob": "y" * 10000}, None, 200).for_model(limit=200)
    assert len(text) < 260 and text.endswith('"truncated"')


async def test_against_the_mock_shop_backend(allow_shop):
    app = create_app(Store())
    live = HttpToolExecutor(transport=ASGITransport(app=app))
    own = await live.execute(
        ToolEndpoint("GET", "http://shop/orders/{order_id}"), {"order_id": "KS-1001"}, CONTEXT
    )
    foreign = await live.execute(
        ToolEndpoint("GET", "http://shop/orders/{order_id}"),
        {"order_id": "KS-2001"},
        CONTEXT,
    )
    assert own.ok and own.data["status"] == "delivered"
    assert not foreign.ok and foreign.status_code == 404
