# Demo scenarios

Four end-to-end scenarios on the KargoShop demo tenant. They run against the real stack: API, worker, mock shop, PostgreSQL and the model.

## Setup

```bash
docker compose up -d --build                      # api, worker, mock shop, postgres, redis
uv sync
uv run python -m mabeyn.cli seed-kargoshop --shop-url http://kargoshop-mock:9000
```

The seed prompts for the demo admin password (`admin@kargoshop.example`) and registers the five shop tools. Get an admin token and a customer session:

```bash
ADMIN=$(curl -s -X POST localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"email": "admin@kargoshop.example", "password": "<demo password>"}' | jq -r .access_token)
KEY=$(curl -s -X POST localhost:8000/api-keys -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"name": "demo"}' | jq -r .key)
CUSTOMER=$(curl -s -X POST localhost:8000/sessions -H "X-API-Key: $KEY" \
  -H 'content-type: application/json' -d '{"customer_id": "customer-1"}' | jq -r .token)
chat() { curl -sN -X POST localhost:8000/chat -H "Authorization: Bearer $CUSTOMER" \
  -H 'content-type: application/json' -d "{\"message\": \"$1\"}"; }
```

The mock shop knows `customer-1` (orders KS-1001 delivered, KS-1002 shipped, KS-1003 processing), `customer-2` and `customer-3` (`seed/kargoshop/orders.json`).

## 1. Knowledge question

```bash
chat "How many days do I have to return a product?"
```

Routed to the knowledge agent. The answer streams as `token` events and ends with `citations` such as `[İade Politikası:İade Süresi]` (the demo documents are Turkish). No tool is called.

## 2. Order lookup

```bash
chat "Where is order KS-1002?"
```

Routed to the action agent, which calls `get_order_status`. The shop receives the customer id from the session, never from the model, so asking about another customer's order (`KS-2001`) returns "not found".

## 3. Refund with human approval

```bash
chat "I want to return order KS-1001. The earbuds are unopened, please refund the full amount."
```

The action agent calls `request_refund`, a `sensitive` tool. Nothing is sent to the shop. The stream contains an `approval` event with an `approval_id`, and the customer is told the request is under review. An admin then decides:

```bash
curl -s localhost:8000/approvals?status=pending -H "Authorization: Bearer $ADMIN" | jq
curl -s -X POST localhost:8000/approvals/<approval_id> -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"decision": "approve"}' | jq
```

Approving runs the tool and returns the customer-facing answer; rejecting never runs it. Either way the answer is stored in the conversation, and the customer fetches it with `GET /chat/<conversation_id>`. The pending approval is kept in PostgreSQL, so it survives an API restart.

## 4. Human handoff

```bash
chat "I want to talk to a human, I am very upset."
```

Routed to the escalation node. The conversation becomes `escalated` (visible in `GET /conversations`), the stream contains an `escalated` event, and later messages in that conversation are stored for a human instead of being answered.
