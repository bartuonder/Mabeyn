"""add the per-tenant semantic answer cache

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

TENANT_SETTING = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "answer_cache",
        sa.Column("id", sa.Uuid(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("question_hash", sa.String(64), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("citations", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), server_default="0", nullable=False),
        sa.UniqueConstraint("tenant_id", "prompt_hash", "question_hash"),
    )
    op.create_index("ix_answer_cache_tenant_id", "answer_cache", ["tenant_id"])
    op.execute(
        "CREATE INDEX ix_answer_cache_embedding ON answer_cache "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("ALTER TABLE answer_cache ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON answer_cache "
        f"USING (tenant_id = {TENANT_SETTING}) WITH CHECK (tenant_id = {TENANT_SETTING})"
    )


def downgrade() -> None:
    op.drop_table("answer_cache")
