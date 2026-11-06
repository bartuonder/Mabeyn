"""add approval webhook settings, delivery tracking and a narrow lookup for the worker

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

from mabeyn.core.config import get_settings

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = make_url(get_settings().database_url).username
    quoted_role = op.get_bind().dialect.identifier_preparer.quote(role)
    op.add_column("tenant_settings", sa.Column("approval_webhook_url", sa.Text(), nullable=True))
    op.add_column("tenant_settings", sa.Column("approval_webhook_secret", sa.Text(), nullable=True))
    op.add_column("approvals", sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "approvals",
        sa.Column("notify_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    # The worker has no tenant context, so this function only joins pending approvals with the
    # webhook of the same tenant.
    op.execute(
        """
        CREATE FUNCTION unnotified_approvals(p_limit integer, p_max_attempts integer)
        RETURNS TABLE (tenant_id uuid, approval_id uuid, webhook_url text, webhook_secret text)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
        AS $$
            SELECT a.tenant_id, a.id, s.approval_webhook_url, s.approval_webhook_secret
            FROM approvals a
            JOIN tenant_settings s ON s.tenant_id = a.tenant_id
            WHERE a.status = 'pending' AND a.notified_at IS NULL
              AND a.notify_attempts < p_max_attempts
              AND s.approval_webhook_url IS NOT NULL
            ORDER BY a.created_at
            LIMIT p_limit
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION unnotified_approvals(integer, integer) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION unnotified_approvals(integer, integer) TO {quoted_role}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS unnotified_approvals(integer, integer)")
    op.drop_column("approvals", "notify_attempts")
    op.drop_column("approvals", "notified_at")
    op.drop_column("tenant_settings", "approval_webhook_secret")
    op.drop_column("tenant_settings", "approval_webhook_url")
