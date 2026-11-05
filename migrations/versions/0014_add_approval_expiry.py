"""let approvals expire and add a narrow lookup for the expired ones

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import make_url

from mabeyn.core.config import get_settings

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    role = make_url(get_settings().database_url).username
    quoted_role = op.get_bind().dialect.identifier_preparer.quote(role)
    op.add_column("approvals", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "UPDATE approvals SET expires_at = created_at + interval '72 hours' "
        "WHERE status = 'pending'"
    )
    op.drop_constraint("approvalstatus", "approvals", type_="check")
    op.create_check_constraint(
        "approvalstatus",
        "approvals",
        "status IN ('pending', 'approved', 'rejected', 'expired')",
    )
    # The background job has no tenant context yet, so this read-only function returns ids only.
    op.execute(
        """
        CREATE FUNCTION expired_approvals(p_limit integer)
        RETURNS TABLE (tenant_id uuid, approval_id uuid)
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
        AS $$
            SELECT a.tenant_id, a.id
            FROM approvals a
            WHERE a.status = 'pending' AND a.expires_at IS NOT NULL AND a.expires_at < now()
            ORDER BY a.expires_at
            LIMIT p_limit
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION expired_approvals(integer) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION expired_approvals(integer) TO {quoted_role}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS expired_approvals(integer)")
    op.execute("UPDATE approvals SET status = 'rejected' WHERE status = 'expired'")
    op.drop_constraint("approvalstatus", "approvals", type_="check")
    op.create_check_constraint(
        "approvalstatus", "approvals", "status IN ('pending', 'approved', 'rejected')"
    )
    op.drop_column("approvals", "expires_at")
