"""Browser walk-through of the admin panel (runs in Docker, see make admin-e2e).

Needs the running demo stack. A real headless Chromium signs in, decides a refund that a customer
asked for through the API, uploads and deletes a document, registers a tool, edits the firewall,
creates and revokes an API key, and signs out. Screenshots land in $SHOTS.

The refund is for customer-2's order KS-2002. The shop refunds an order once and the firewall allows
five changes per customer and hour, so restart the mock shop (docker compose restart kargoshop-mock)
and do not run it more than five times an hour.
"""

import json
import os
import urllib.request

from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=20_000)

PANEL = "http://localhost:3100"
API = "http://host.docker.internal:8000"
SHOTS = os.environ.get("SHOTS", "/work/shots")
PASSWORD = os.environ.get("ADMIN_PASSWORD", "demo-admin-password")


def call(path, method="GET", body=None, headers=None):
    request = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"content-type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(request) as response:
        return response.read().decode()


def customer_asks_for_refund():
    admin = json.loads(
        call("/auth/login", "POST", {"email": "admin@kargoshop.example", "password": PASSWORD})
    )
    auth = {"Authorization": f"Bearer {admin['access_token']}"}
    key = json.loads(call("/api-keys", "POST", {"name": "admin-e2e"}, auth))["key"]
    session = json.loads(
        call("/sessions", "POST", {"customer_id": "customer-2"}, {"X-API-Key": key})
    )["token"]
    call(
        "/chat",
        "POST",
        {"message": "Please refund order KS-2002, 8999.00 TRY. I changed my mind."},
        {"Authorization": f"Bearer {session}"},
    )
    keys = json.loads(call("/api-keys", headers=auth))
    for k in keys:
        if k["name"] == "admin-e2e" and not k["revoked_at"]:
            call(f"/api-keys/{k['id']}", "DELETE", headers=auth)


with sync_playwright() as p:
    browser = p.chromium.launch(args=["--host-resolver-rules=MAP localhost host.docker.internal"])
    page = browser.new_page(viewport={"width": 1200, "height": 800})
    page.on("dialog", lambda dialog: dialog.accept())
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    # sign in
    page.goto(PANEL)
    expect(page).to_have_url(PANEL + "/login")
    page.fill("input[name=email]", "admin@kargoshop.example")
    page.fill("input[name=password]", "wrong-password")
    page.click("button[type=submit]")
    expect(page.locator("p.error")).to_contain_text("Wrong e-mail or password")
    assert (
        page.input_value("input[name=email]") == "admin@kargoshop.example"
    )  # kept after the error
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
    page.screenshot(path=f"{SHOTS}/a1-overview.png")
    print("SIGNED IN")

    # a customer asks for a refund; the admin decides it
    customer_asks_for_refund()
    page.goto(PANEL + "/approvals")
    expect(page.get_by_role("link", name="UTC").first).to_be_visible()
    page.screenshot(path=f"{SHOTS}/a2-approvals.png")
    page.get_by_role("link", name="UTC").first.click()
    expect(page.get_by_role("heading", name="Approval request")).to_be_visible()
    page.screenshot(path=f"{SHOTS}/a3-approval.png")
    page.fill("input[name=note]", "Looks fine")
    page.click("button[value=approve]")
    expect(page.locator("p.ok")).to_contain_text("Decision recorded")
    print("APPROVED:", page.inner_text("main h1"))

    # documents
    page.goto(PANEL + "/documents")
    with open("/tmp/panel-e2e.md", "w") as f:
        f.write(
            "# Panel test\n\n## Opening hours\n\nOur support desk is open from 09:00 to 18:00 every weekday.\n"
        )
    page.set_input_files("input[name=file]", "/tmp/panel-e2e.md")
    page.fill("form:has(input[name=file]) input[name=title]", "Panel test document")
    page.click("form:has(input[name=file]) button[type=submit]")
    expect(page.locator("p.ok")).to_contain_text("Uploaded")
    row = page.locator("tr", has_text="Panel test document")
    expect(row.locator(".badge")).to_have_text("ready", timeout=60000)
    page.screenshot(path=f"{SHOTS}/a4-documents.png")
    row.get_by_role("button", name="Delete").click()
    expect(page.locator("tr", has_text="Panel test document")).to_have_count(0)
    print("DOCUMENT UPLOADED AND DELETED")

    # tools
    page.goto(PANEL + "/tools")
    page.fill("input[name=name]", "get_loyalty_points")
    page.fill("input[name=description]", "Look up the loyalty points of the current customer")
    page.fill("input[name=url_template]", "http://kargoshop-mock:9000/orders/{order_id}")
    page.click("button:has-text('Create tool')")
    expect(page.locator("p.ok")).to_contain_text("Tool created")
    page.fill("input[name=name]", "keeps_fields")
    page.fill("input[name=description]", "A second tool that should not be created")
    page.fill("input[name=url_template]", "http://kargoshop-mock:9000/orders")
    page.fill("textarea[name=json_schema]", "{not json")
    page.click("button:has-text('Create tool')")
    expect(page.locator("p.error")).to_contain_text("not valid JSON")
    assert page.input_value("input[name=name]") == "keeps_fields"  # not wiped by the error
    page.screenshot(path=f"{SHOTS}/a5-tools.png")
    page.locator("tr", has_text="get_loyalty_points").get_by_role("button", name="Delete").click()
    expect(page.locator("tr", has_text="get_loyalty_points")).to_have_count(0)
    print("TOOL CREATED AND DELETED")

    # security
    page.goto(PANEL + "/security")
    page.fill("input[name=injection_threshold]", "0.8")
    page.click("button:has-text('Save settings')")
    expect(page.locator("p.ok").first).to_contain_text("Firewall settings saved")
    page.fill("input[name=approval_webhook_url]", "http://10.0.0.5/hook")
    page.click("button:has-text('Save webhook')")
    expect(page.locator("p.error")).to_contain_text("non-public")
    page.fill("input[name=injection_threshold]", "0.7")
    page.click("button:has-text('Save settings')")
    page.screenshot(path=f"{SHOTS}/a6-security.png")
    print("SECURITY SETTINGS OK")

    # conversations and usage
    page.goto(PANEL + "/conversations")
    page.get_by_role("link", name="UTC").first.click()
    expect(page.get_by_text("What the assistant remembers")).to_be_visible()
    page.screenshot(path=f"{SHOTS}/a7-conversation.png")
    page.goto(PANEL + "/usage?days=30")
    expect(page.get_by_role("heading", name="Usage")).to_be_visible()
    page.hover("svg rect.bar")
    page.screenshot(path=f"{SHOTS}/a8-usage.png")

    # keys
    page.goto(PANEL + "/keys")
    page.fill("input[name=name]", "e2e key")
    page.fill("textarea[name=allowed_origins]", "https://shop.example")
    page.click("button:has-text('Create key')")
    expect(page.locator("p.secret-box")).to_contain_text("shown only once")
    page.locator("tr", has_text="e2e key").get_by_role("button", name="Revoke").click()
    expect(
        page.locator("tr", has_text="e2e key").locator(".badge", has_text="revoked")
    ).to_be_visible()
    print("KEY CREATED AND REVOKED")

    # sign out, and the session is really gone
    page.get_by_role("button", name="Sign out").click()
    expect(page).to_have_url(PANEL + "/login")
    page.goto(PANEL + "/approvals")
    expect(page).to_have_url(PANEL + "/login")
    print("SIGNED OUT")
    print("CONSOLE ERRORS:", errors)
    browser.close()
