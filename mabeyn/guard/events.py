import uuid
from typing import Any

from mabeyn.db.models import SecurityAction, SecurityEvent
from mabeyn.db.session import tenant_session
from mabeyn.guard.pii import mask_pii

SNIPPET_CHARS = 160


def snippet(text: str) -> str:
    """A short, personal-data-free excerpt of what was blocked."""
    return mask_pii(text[:SNIPPET_CHARS]).text


async def record_event(
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    layer: str,
    category: str,
    action: SecurityAction,
    detail: dict[str, Any] | None = None,
) -> None:
    async with tenant_session(tenant_id) as session:
        session.add(
            SecurityEvent(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                layer=layer,
                category=category,
                action=action,
                detail=detail or {},
            )
        )
