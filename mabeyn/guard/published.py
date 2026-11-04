import uuid

from sqlalchemy import func, select

from mabeyn.db.models import Chunk
from mabeyn.db.session import tenant_session

_SQUASHED = " -()."


def squash(value: str) -> str:
    return value.lower().translate({ord(c): None for c in _SQUASHED})


async def is_published(tenant_id: uuid.UUID, value: str) -> bool:
    """True when the tenant's own documents contain the value, like a support address."""
    needle = squash(value)
    async with tenant_session(tenant_id) as session:
        found = await session.scalar(
            select(Chunk.id)
            .where(
                func.strpos(func.translate(func.lower(Chunk.content), _SQUASHED, ""), needle) > 0,
                Chunk.flagged.is_(False),
            )
            .limit(1)
        )
    return found is not None
