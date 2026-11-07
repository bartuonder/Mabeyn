"""Take the README screenshots of the running demo stack (run after demo/record.py).

Real Chromium, the shop's storefront with the chat widget and every page of the admin panel, at
1440x900 with a device scale factor of 1.5. Needs the data the recording leaves behind (memory,
security events, an approved refund) and creates one pending approval for the screenshots.
"""

import json
import os
import time
import urllib.request

from playwright.sync_api import Browser, Page, sync_playwright

SHOP = "http://localhost:9100"
PANEL = "http://localhost:3100"
API = os.environ.get("API", "http://host.docker.internal:8000")
OUT = os.path.join(os.environ.get("OUT", "/work/out"), "screenshots")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@kargoshop.example")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "demo-admin-password")
VIEWPORT = {"width": 1440, "height": 900}


def api(
    path: str, method: str = "GET", body: object = None, headers: dict[str, str] | None = None
) -> object:
    request = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"content-type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request) as response:  # noqa: S310
        raw = response.read()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:  # the chat endpoint streams server-sent events
        return raw.decode()


def request_a_refund() -> None:
    """A customer asks for a refund through the API so that the approval queue is not empty."""
    login = api("/auth/login", "POST", {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    auth = {"Authorization": f"Bearer {login['access_token']}"}  # type: ignore[index]
    if api("/approvals?status=pending", headers=auth):
        return  # an earlier run already left one in the queue
    key = api("/api-keys", "POST", {"name": "screenshots"}, auth)["key"]  # type: ignore[index]
    session = api("/sessions", "POST", {"customer_id": "customer-3"}, {"X-API-Key": key})["token"]  # type: ignore[index]
    api(
        "/chat",
        "POST",
        {"message": "Please refund order KS-3002, I changed my mind."},
        {"Authorization": f"Bearer {session}"},
    )
    for item in api("/api-keys", headers=auth):  # type: ignore[union-attr]
        if item["name"] == "screenshots" and not item["revoked_at"]:
            api(f"/api-keys/{item['id']}", "DELETE", headers=auth)


def new_page(browser: Browser, scheme: str = "light") -> Page:
    context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1.5, color_scheme=scheme)  # type: ignore[arg-type]
    return context.new_page()


def shot(page: Page, name: str, *, full: bool = False) -> None:
    page.wait_for_load_state("load")
    page.wait_for_timeout(1800)  # the panel keeps prefetching, so "networkidle" never comes
    page.screenshot(path=os.path.join(OUT, name), full_page=full)
    print("saved", name)


def chat(page: Page, question: str) -> None:
    box = page.locator("input[aria-label=Message]")
    before = page.locator(".msg.bot").count()
    box.fill(question)
    box.press("Enter")
    deadline = time.time() + 90
    while time.time() < deadline:
        page.wait_for_timeout(500)
        if box.is_enabled() and page.locator(".msg.bot").count() >= before + 1:
            break
    page.wait_for_timeout(800)


def sign_in(page: Page) -> None:
    page.goto(f"{PANEL}/login")
    page.fill("input[name=email]", ADMIN_EMAIL)
    page.fill("input[name=password]", ADMIN_PASSWORD)
    page.click("button[type=submit]")
    page.get_by_role("heading", name="Overview").wait_for(timeout=90000)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    request_a_refund()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--host-resolver-rules=MAP localhost host.docker.internal"]
        )

        # The customer's side: the storefront with the chat widget.
        page = new_page(browser)
        page.goto(SHOP)
        page.evaluate(
            "fetch('/login', {method: 'POST', headers: {'Content-Type': 'application/json'},"
            " body: JSON.stringify({customer: 'customer-1'})})"
        )
        page.wait_for_timeout(500)
        page.goto(SHOP)
        page.wait_for_timeout(1500)
        page.locator("button.bubble").click()
        chat(page, "KKTC'ye kargo kaç günde gelir?")
        chat(page, "Where is my order KS-1002?")
        shot(page, "01-storefront-chat.png")
        page.locator("button[aria-label=Close]").click()
        page.locator("#orders").scroll_into_view_if_needed()
        page.evaluate("window.scrollBy(0, 160)")
        shot(page, "02-storefront-orders.png")

        # The admin's side.
        page = new_page(browser)
        sign_in(page)
        shot(page, "03-admin-overview.png")

        page.goto(f"{PANEL}/approvals")
        page.locator("table tbody tr td a").first.click()
        page.get_by_role("heading", name="Approval request").wait_for()
        shot(page, "04-admin-approval.png")

        page.goto(f"{PANEL}/conversations")
        page.locator("tr", has_text="customer-3").first.locator("a").click()
        page.get_by_text("What the assistant remembers").wait_for()
        shot(page, "05-admin-conversation-memory.png", full=True)

        page.goto(f"{PANEL}/documents")
        shot(page, "06-admin-documents.png")

        page.goto(f"{PANEL}/tools")
        shot(page, "07-admin-tools.png")

        page.goto(f"{PANEL}/security")
        shot(page, "08-admin-security.png", full=True)

        page.goto(f"{PANEL}/usage?days=7")
        shot(page, "09-admin-usage.png")

        # The same overview in dark mode: the panel follows the system setting.
        dark = new_page(browser, "dark")
        sign_in(dark)
        shot(dark, "10-admin-overview-dark.png")
        browser.close()


if __name__ == "__main__":
    main()
