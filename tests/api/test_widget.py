from httpx import AsyncClient


async def test_the_widget_script_is_served_for_any_site_to_embed(client: AsyncClient):
    response = await client.get("/widget.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert "mabeynWidget" in response.text and "/chat" in response.text


async def test_browsers_may_call_the_chat_api_from_another_origin(client: AsyncClient):
    preflight = await client.options(
        "/chat",
        headers={
            "Origin": "https://shop.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "*"
    allowed = preflight.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed and "x-api-key" in allowed
    assert "access-control-allow-credentials" not in preflight.headers
