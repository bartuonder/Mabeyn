import hashlib
import hmac
import json
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy import select, text, update

from mabeyn.core.config import get_settings
from mabeyn.db.models import Approval, ApprovalStatus, TenantSettings
from mabeyn.db.session import anonymous_session, tenant_session
from mabeyn.ingestion.fetch import FetchError, Resolver, ensure_public, resolve_host

log = structlog.get_logger()

BATCH = 50
SIGNATURE_HEADER = "X-Mabeyn-Signature"
EVENT_HEADER = "X-Mabeyn-Event"


class WebhookError(Exception):
    pass


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _allowed_hosts() -> set[str]:
    raw = get_settings().tool_allowed_hosts
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


async def check_url(url: str, resolver: Resolver = resolve_host) -> None:
    """A webhook may only point at a public host (or one the operator allowed for tools)."""
    host = (urlparse(url).hostname or "").lower()
    if host in _allowed_hosts():
        return
    try:
        await ensure_public(url, resolver)
    except FetchError as exc:
        raise WebhookError(str(exc)) from exc


async def deliver(
    url: str,
    secret: str,
    event: str,
    payload: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver = resolve_host,
) -> None:
    """POST a signed JSON event; raises WebhookError unless the receiver answers 2xx."""
    await check_url(url, resolver)
    body = json.dumps({"event": event, **payload}, separators=(",", ":")).encode()
    headers = {
        "content-type": "application/json",
        EVENT_HEADER: event,
        SIGNATURE_HEADER: sign(secret, body),
    }
    try:
        async with httpx.AsyncClient(
            timeout=get_settings().webhook_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as exc:
        raise WebhookError(f"{exc.__class__.__name__} while calling the webhook") from exc
    if not 200 <= response.status_code < 300:
        raise WebhookError(f"the webhook answered {response.status_code}")


async def save_webhook(
    tenant_id: uuid.UUID, url: str | None, resolver: Resolver = resolve_host
) -> str | None:
    """Set or clear the approval webhook; a new secret is returned once when a URL is set.

    Approvals that were already pending are marked as notified, so setting a webhook does not
    announce old requests.
    """
    if url is not None:
        await check_url(url, resolver)
    secret = secrets.token_urlsafe(32) if url is not None else None
    async with tenant_session(tenant_id) as session:
        row = await session.get(TenantSettings, tenant_id)
        if row is None:
            row = TenantSettings(tenant_id=tenant_id)
            session.add(row)
        row.approval_webhook_url = url
        row.approval_webhook_secret = secret
        if url is not None:
            await session.execute(
                update(Approval)
                .where(Approval.status == ApprovalStatus.PENDING, Approval.notified_at.is_(None))
                .values(notified_at=datetime.now(UTC))
            )
    return secret


async def load_webhook_url(tenant_id: uuid.UUID) -> str | None:
    async with tenant_session(tenant_id) as session:
        row = await session.get(TenantSettings, tenant_id)
        return row.approval_webhook_url if row else None


async def _unnotified(limit: int, max_attempts: int) -> list[Any]:
    async with anonymous_session() as session:
        rows = await session.execute(
            text("SELECT * FROM unnotified_approvals(:n, :attempts)"),
            {"n": limit, "attempts": max_attempts},
        )
        return list(rows)


async def notify_pending_approvals(
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver = resolve_host,
    limit: int = BATCH,
) -> int:
    """Tell each tenant's webhook about approvals it has not heard of; returns how many went out."""
    sent = 0
    for row in await _unnotified(limit, get_settings().webhook_max_attempts):
        async with tenant_session(row.tenant_id) as session:
            approval = await session.scalar(
                select(Approval).where(Approval.id == row.approval_id).with_for_update()
            )
            if approval is None or approval.status is not ApprovalStatus.PENDING:
                continue
            payload = {
                "approval_id": str(approval.id),
                "conversation_id": str(approval.conversation_id),
                "tool": approval.payload.get("tool"),
                "summary": approval.payload.get("summary"),
                "created_at": approval.created_at.isoformat(),
                "expires_at": approval.expires_at.isoformat() if approval.expires_at else None,
            }
            try:
                await deliver(
                    row.webhook_url,
                    row.webhook_secret,
                    "approval.pending",
                    payload,
                    transport=transport,
                    resolver=resolver,
                )
            except WebhookError as exc:
                approval.notify_attempts += 1
                log.warning("approval_webhook_failed", approval_id=str(approval.id), error=str(exc))
                continue
            approval.notified_at = datetime.now(UTC)
            sent += 1
    return sent
