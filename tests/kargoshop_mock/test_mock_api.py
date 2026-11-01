import pytest
from httpx import ASGITransport, AsyncClient

from apps.kargoshop_mock.main import Store, create_app


@pytest.fixture
async def shop():
    app = create_app(Store())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://shop") as client:
        client.store = app.state.store  # type: ignore[attr-defined]
        yield client


async def test_orders_are_listed_per_customer_newest_first(shop):
    orders = (await shop.get("/orders", params={"customer_id": "customer-1"})).json()
    assert [o["id"] for o in orders] == ["KS-1003", "KS-1002", "KS-1001"]
    shipped = (
        await shop.get("/orders", params={"customer_id": "customer-1", "status": "shipped"})
    ).json()
    assert [o["id"] for o in shipped] == ["KS-1002"]


async def test_other_customers_orders_look_missing(shop):
    own = await shop.get("/orders/KS-1001", params={"customer_id": "customer-1"})
    foreign = await shop.get("/orders/KS-1001", params={"customer_id": "customer-2"})
    missing = await shop.get("/orders/KS-9999", params={"customer_id": "customer-2"})
    assert own.status_code == 200 and own.json()["tracking_number"] == "AK100100"
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


async def test_ticket_creation(shop):
    response = await shop.post(
        "/tickets",
        json={"customer_id": "customer-1", "subject": "Late parcel", "description": "Not here yet"},
    )
    assert response.status_code == 201 and response.json()["status"] == "open"
    assert len(shop.store.tickets) == 1


async def test_refund_for_a_recent_delivered_order(shop):
    response = await shop.post(
        "/orders/KS-1001/refund",
        json={"customer_id": "customer-1", "amount": 899.90, "reason": "Changed my mind"},
    )
    assert response.status_code == 201 and response.json()["status"] == "initiated"
    again = await shop.post(
        "/orders/KS-1001/refund",
        json={"customer_id": "customer-1", "amount": 10, "reason": "Duplicate attempt"},
    )
    assert again.status_code == 409


@pytest.mark.parametrize(
    ("order", "customer", "amount", "status_code"),
    [
        ("KS-1002", "customer-1", 100, 409),  # not delivered yet
        ("KS-2001", "customer-2", 100, 409),  # return window passed
        ("KS-1001", "customer-1", 5000, 422),  # more than the order total
        ("KS-1001", "customer-2", 100, 404),  # someone else's order
    ],
)
async def test_refund_rules(shop, order, customer, amount, status_code):
    response = await shop.post(
        f"/orders/{order}/refund",
        json={"customer_id": customer, "amount": amount, "reason": "Not needed anymore"},
    )
    assert response.status_code == status_code
    assert shop.store.refunds == []


async def test_address_changes_only_while_processing(shop):
    new = {
        "customer_id": "customer-1",
        "line": "Yeni Sk. 4",
        "city": "Bursa",
        "postal_code": "16000",
    }
    ok = await shop.put("/orders/KS-1003/address", json=new)
    assert ok.status_code == 200 and ok.json()["address"]["city"] == "Bursa"
    shipped = await shop.put("/orders/KS-1002/address", json=new)
    assert shipped.status_code == 409


async def test_unknown_fields_are_rejected(shop):
    response = await shop.post(
        "/tickets",
        json={"customer_id": "c", "subject": "abc", "description": "abc", "is_admin": True},
    )
    assert response.status_code == 422


async def test_each_store_is_independent():
    first, second = Store(), Store()
    first.orders["KS-1001"]["status"] = "cancelled"
    assert second.orders["KS-1001"]["status"] == "delivered"
