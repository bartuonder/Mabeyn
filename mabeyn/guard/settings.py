import uuid
from dataclasses import dataclass
from typing import Any

from mabeyn.db.models import TenantSettings
from mabeyn.db.session import tenant_session

DEFAULT_THRESHOLD = 0.7
DEFAULT_RATE_LIMIT = 5
DEFAULT_RATE_WINDOW_MINUTES = 60


@dataclass(frozen=True)
class GuardSettings:
    firewall_enabled: bool = True
    injection_threshold: float = DEFAULT_THRESHOLD
    write_rate_limit: int = DEFAULT_RATE_LIMIT
    write_rate_window_minutes: int = DEFAULT_RATE_WINDOW_MINUTES
    tool_allowlist: tuple[str, ...] | None = None


def _from_row(row: TenantSettings) -> GuardSettings:
    allowlist = row.tool_allowlist
    return GuardSettings(
        firewall_enabled=row.firewall_enabled,
        injection_threshold=row.injection_threshold,
        write_rate_limit=row.write_rate_limit,
        write_rate_window_minutes=row.write_rate_window_minutes,
        tool_allowlist=tuple(allowlist) if allowlist is not None else None,
    )


async def load_guard_settings(tenant_id: uuid.UUID) -> GuardSettings:
    async with tenant_session(tenant_id) as session:
        row = await session.get(TenantSettings, tenant_id)
        return _from_row(row) if row else GuardSettings()


async def save_guard_settings(tenant_id: uuid.UUID, updates: dict[str, Any]) -> GuardSettings:
    async with tenant_session(tenant_id) as session:
        row = await session.get(TenantSettings, tenant_id)
        if row is None:
            row = TenantSettings(tenant_id=tenant_id)
            session.add(row)
        for name, value in updates.items():
            setattr(
                row, name, list(value) if name == "tool_allowlist" and value is not None else value
            )
        await session.flush()
        await session.refresh(row)
        return _from_row(row)
