"""Load test of the chat endpoint.

Each simulated customer opens a session with a secret API key and then keeps asking. Most
questions come from a small pool of knowledge questions that many customers repeat (what the
cache is for); the rest are order lookups that are never cached.

    LOAD_API_KEY=mbn_sec_... uv run locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 40s \
        --host http://localhost:8000
"""

import json
import os
import random

from locust import HttpUser, between, task

KNOWLEDGE = [
    "How many days do I have to return a product?",
    "How long does delivery to KKTC take?",
    "Can I pay in instalments?",
    "How do I track my parcel?",
    "How do I delete my account?",
    "Do you ship to Germany?",
    "What does the warranty cover?",
    "How can I contact customer service?",
    "How do gift cards work?",
    "When are loyalty points added?",
]
ORDERS = [
    ("customer-1", "Where is order KS-1002?"),
    ("customer-1", "What is the status of KS-1003?"),
    ("customer-2", "KS-2002 siparişim ne zaman gelecek?"),
    ("customer-3", "Was my order KS-3001 cancelled?"),
]
KNOWLEDGE_SHARE = 0.7


class Customer(HttpUser):
    wait_time = between(8, 14)

    def on_start(self) -> None:
        self.customer_id = f"load-{random.randrange(10**9)}"  # noqa: S311
        self.order_customer: str | None = None
        self.token = self._session(self.customer_id)

    def _session(self, customer_id: str) -> str:
        response = self.client.post(
            "/sessions",
            json={"customer_id": customer_id},
            headers={"X-API-Key": os.environ["LOAD_API_KEY"]},
            name="POST /sessions",
        )
        return str(response.json()["token"])

    def _chat(self, token: str, message: str, name: str) -> None:
        with self.client.post(
            "/chat",
            json={"message": message},
            headers={"Authorization": f"Bearer {token}"},
            name=name,
            catch_response=True,
        ) as response:
            body = response.text
            if response.status_code != 200:
                response.failure(f"status {response.status_code}")
            elif "event: error" in body:
                response.failure("error event")
            elif "event: done" not in body:
                response.failure("no done event")
            else:
                done = body.split("event: done\ndata: ", 1)[1].split("\n", 1)[0]
                if not json.loads(done)["answer"]:
                    response.failure("empty answer")

    @task
    def ask(self) -> None:
        if random.random() < KNOWLEDGE_SHARE:  # noqa: S311
            self._chat(self.token, random.choice(KNOWLEDGE), "chat: knowledge")  # noqa: S311
        else:
            customer, message = random.choice(ORDERS)  # noqa: S311
            token = self.order_customer or self._session(customer)
            self.order_customer = token
            self._chat(token, message, "chat: order lookup")
