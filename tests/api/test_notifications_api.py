from httpx import AsyncClient

from tests.api.test_api_keys import auth_headers


async def test_the_webhook_is_set_cleared_and_the_secret_is_shown_once(
    client: AsyncClient, tenant_x
):
    headers = await auth_headers(client, tenant_x)
    assert (await client.get("/settings/notifications", headers=headers)).json() == {
        "approval_webhook_url": None,
        "signing_secret": None,
    }
    private = await client.put(
        "/settings/notifications",
        json={"approval_webhook_url": "http://10.0.0.5/hook"},
        headers=headers,
    )
    assert private.status_code == 422
    not_a_url = await client.put(
        "/settings/notifications", json={"approval_webhook_url": "ftp://x"}, headers=headers
    )
    assert not_a_url.status_code == 422
    cleared = await client.put(
        "/settings/notifications", json={"approval_webhook_url": None}, headers=headers
    )
    assert cleared.status_code == 200 and cleared.json()["signing_secret"] is None


async def test_an_allowed_host_gets_a_secret_only_in_the_response_that_sets_it(
    client: AsyncClient, tenant_x, monkeypatch
):
    monkeypatch.setattr("mabeyn.notify.webhook._allowed_hosts", lambda: {"hooks.test"})
    headers = await auth_headers(client, tenant_x)
    url = "https://hooks.test/approvals"
    saved = await client.put(
        "/settings/notifications", json={"approval_webhook_url": url}, headers=headers
    )
    assert saved.status_code == 200 and len(saved.json()["signing_secret"]) >= 32
    shown = (await client.get("/settings/notifications", headers=headers)).json()
    assert shown == {"approval_webhook_url": url, "signing_secret": None}
