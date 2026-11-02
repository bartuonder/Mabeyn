"""add a narrow lookup for conversations waiting for memory extraction

Revision ID: 0008
Revises: 0007
"""

from alembic import op
from sqlalchemy.engine import make_url

from mabeyn.core.config import get_settings

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = make_url(get_settings().database_url).username
    quoted_role = op.get_bind().dialect.identifier_preparer.quote(role)
    # The background job has no tenant context yet, so this read-only function returns ids only.
    op.execute(
        """
        CREATE FUNCTION memory_idle_conversations(p_idle_minutes integer, p_limit integer)
        RETURNS TABLE (tenant_id uuid, conversation_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
        AS $$
            SELECT c.tenant_id, c.id
            FROM conversations c
            WHERE c.memory_extracted_at IS NULL
              AND EXISTS (
                  SELECT 1 FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user'
              )
              AND (
                  SELECT max(m.created_at) FROM messages m WHERE m.conversation_id = c.id
              ) < now() - make_interval(mins => p_idle_minutes)
            ORDER BY c.created_at
            LIMIT p_limit
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION memory_idle_conversations(integer, integer) FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION memory_idle_conversations(integer, integer) TO {quoted_role}"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS memory_idle_conversations(integer, integer)")
