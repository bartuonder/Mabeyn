from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from apps.api.deps import Admin, TenantDb
from apps.api.schemas import FirewallSettingsOut, FirewallSettingsUpdate, SecurityEventOut
from mabeyn.db.models import SecurityAction, SecurityEvent
from mabeyn.guard.settings import GuardSettings, load_guard_settings, save_guard_settings

router = APIRouter(tags=["security"])


@router.get("/security-events")
async def list_security_events(
    db: TenantDb,
    layer: str | None = None,
    action: SecurityAction | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[SecurityEventOut]:
    """Every block and flag the firewall and the memory hygiene produced, newest first."""
    statement = select(SecurityEvent).order_by(SecurityEvent.created_at.desc()).limit(limit)
    if layer is not None:
        statement = statement.where(SecurityEvent.layer == layer)
    if action is not None:
        statement = statement.where(SecurityEvent.action == action)
    return [SecurityEventOut.model_validate(row) for row in await db.scalars(statement)]


def _out(settings: GuardSettings) -> FirewallSettingsOut:
    allowlist = list(settings.tool_allowlist) if settings.tool_allowlist is not None else None
    return FirewallSettingsOut(
        firewall_enabled=settings.firewall_enabled,
        injection_threshold=settings.injection_threshold,
        write_rate_limit=settings.write_rate_limit,
        write_rate_window_minutes=settings.write_rate_window_minutes,
        tool_allowlist=allowlist,
    )


@router.get("/settings/firewall")
async def get_firewall_settings(admin: Admin) -> FirewallSettingsOut:
    return _out(await load_guard_settings(admin.tenant_id))


@router.put("/settings/firewall")
async def update_firewall_settings(
    body: FirewallSettingsUpdate, admin: Admin
) -> FirewallSettingsOut:
    """Change only the fields that are sent; `tool_allowlist: null` allows every tool again."""
    updates = body.model_dump(exclude_unset=True)
    updates = {k: v for k, v in updates.items() if v is not None or k == "tool_allowlist"}
    return _out(await save_guard_settings(admin.tenant_id, updates))
