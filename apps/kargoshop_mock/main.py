import copy
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

ORDERS_FILE = Path(__file__).resolve().parents[2] / "seed" / "kargoshop" / "orders.json"
RETURN_WINDOW_DAYS = 30


class Address(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: str = Field(min_length=3, max_length=200)
    city: str = Field(min_length=2, max_length=80)
    postal_code: str = Field(min_length=3, max_length=12)


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    subject: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=2000)


class RefundIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    amount: float = Field(gt=0)
    reason: str = Field(min_length=3, max_length=500)


class AddressChangeIn(Address):
    customer_id: str


class Store:
    """In-memory state of the fictional shop; each instance is independent."""

    def __init__(self, path: Path = ORDERS_FILE) -> None:
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.today = date.fromisoformat(raw["today"])
        self.orders: dict[str, dict[str, Any]] = {o["id"]: o for o in copy.deepcopy(raw["orders"])}
        self.tickets: list[dict[str, Any]] = []
        self.refunds: list[dict[str, Any]] = []
        self.replays: dict[str, tuple[int, Any]] = {}

    def order_for(self, order_id: str, customer_id: str) -> dict[str, Any]:
        order = self.orders.get(order_id)
        # Other customers' orders look exactly like missing ones.
        if order is None or order["customer_id"] != customer_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
        return order


IdempotencyKey = Annotated[str | None, Header()]


def replayed(store: Store, scope: str, key: str | None) -> JSONResponse | None:
    """Answer a repeated request with the original response instead of acting again."""
    if key and f"{scope}:{key}" in store.replays:
        code, body = store.replays[f"{scope}:{key}"]
        return JSONResponse(body, status_code=code)
    return None


def remember(store: Store, scope: str, key: str | None, code: int, body: Any) -> Any:
    if key:
        store.replays[f"{scope}:{key}"] = (code, body)
    return body


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store()
    app = FastAPI(title="KargoShop mock backend", version="0.1.0")
    app.state.store = store

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/orders")
    async def list_orders(
        customer_id: str,
        order_status: Annotated[str | None, Query(alias="status")] = None,
    ) -> list[dict[str, Any]]:
        orders = [o for o in store.orders.values() if o["customer_id"] == customer_id]
        if order_status:
            orders = [o for o in orders if o["status"] == order_status]
        return sorted(orders, key=lambda o: o["placed_at"], reverse=True)

    @app.get("/refunds")
    async def list_refunds(customer_id: str) -> list[dict[str, Any]]:
        owned = {o["id"] for o in store.orders.values() if o["customer_id"] == customer_id}
        return [r for r in store.refunds if r["order_id"] in owned]

    @app.get("/orders/{order_id}")
    async def get_order(order_id: str, customer_id: str) -> dict[str, Any]:
        return store.order_for(order_id, customer_id)

    @app.post("/tickets", status_code=status.HTTP_201_CREATED, response_model=None)
    async def create_ticket(body: TicketIn, idempotency_key: IdempotencyKey = None) -> Any:
        if (again := replayed(store, "ticket", idempotency_key)) is not None:
            return again
        ticket = {"id": f"T-{len(store.tickets) + 1:04d}", "status": "open", **body.model_dump()}
        store.tickets.append(ticket)
        return remember(store, "ticket", idempotency_key, 201, ticket)

    @app.post("/orders/{order_id}/refund", status_code=status.HTTP_201_CREATED, response_model=None)
    async def request_refund(
        order_id: str, body: RefundIn, idempotency_key: IdempotencyKey = None
    ) -> Any:
        if (again := replayed(store, "refund", idempotency_key)) is not None:
            return again
        order = store.order_for(order_id, body.customer_id)
        if order["status"] != "delivered":
            raise HTTPException(status.HTTP_409_CONFLICT, "Only delivered orders can be refunded")
        delivered = date.fromisoformat(order["delivered_at"])
        if (store.today - delivered).days > RETURN_WINDOW_DAYS:
            raise HTTPException(status.HTTP_409_CONFLICT, "The 30 day return window has passed")
        if body.amount > order["total"]:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Amount exceeds order total")
        if any(r["order_id"] == order_id for r in store.refunds):
            raise HTTPException(status.HTTP_409_CONFLICT, "A refund already exists for this order")
        refund = {
            "id": f"R-{len(store.refunds) + 1:04d}",
            "order_id": order_id,
            "amount": body.amount,
            "reason": body.reason,
            "status": "initiated",
        }
        store.refunds.append(refund)
        return remember(store, "refund", idempotency_key, 201, refund)

    @app.put("/orders/{order_id}/address", response_model=None)
    async def change_address(
        order_id: str, body: AddressChangeIn, idempotency_key: IdempotencyKey = None
    ) -> Any:
        if (again := replayed(store, "address", idempotency_key)) is not None:
            return again
        order = store.order_for(order_id, body.customer_id)
        if order["status"] != "processing":
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "The address can only change while the order is processing",
            )
        order["address"] = body.model_dump(exclude={"customer_id"})
        return remember(store, "address", idempotency_key, 200, order)

    return app


app = create_app()
