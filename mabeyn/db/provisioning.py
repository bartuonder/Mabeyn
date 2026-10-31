import re
import uuid

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from mabeyn.auth.passwords import hash_password
from mabeyn.db.models import AdminUser, Tenant

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")
MIN_PASSWORD_LENGTH = 12


async def create_tenant(
    engine: AsyncEngine, name: str, slug: str, admin_email: str, admin_password: str
) -> uuid.UUID:
    """Create a tenant and its first admin. Needs the owner connection."""
    if not SLUG_PATTERN.match(slug):
        raise ValueError("slug must be 3-64 chars of lowercase letters, digits and dashes")
    if len(admin_password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    async with AsyncSession(engine) as session, session.begin():
        tenant = Tenant(name=name, slug=slug)
        session.add(tenant)
        await session.flush()
        session.add(
            AdminUser(
                tenant_id=tenant.id,
                email=admin_email.strip().lower(),
                password_hash=hash_password(admin_password),
            )
        )
        return tenant.id
