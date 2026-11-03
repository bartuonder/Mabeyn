"""add per-tenant firewall settings

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

TENANT_SETTING = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("firewall_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("injection_threshold", sa.Float(), server_default="0.7", nullable=False),
        sa.Column("write_rate_limit", sa.Integer(), server_default="5", nullable=False),
        sa.Column("write_rate_window_minutes", sa.Integer(), server_default="60", nullable=False),
        sa.Column("tool_allowlist", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.execute("ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON tenant_settings "
        f"USING (tenant_id = {TENANT_SETTING}) WITH CHECK (tenant_id = {TENANT_SETTING})"
    )


def downgrade() -> None:
    op.drop_table("tenant_settings")
