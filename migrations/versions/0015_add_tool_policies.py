"""add argument policies to tool definitions

Revision ID: 0015
Revises: 0014
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

LOOKUP = {"lookup_tool": "get_order_status", "id_argument": "order_id"}
LEGACY = {
    "request_refund": [
        {
            "type": "owned_record",
            **LOOKUP,
            "message": "that order does not belong to this customer",
        },
        {
            "type": "max_amount",
            **LOOKUP,
            "amount_argument": "amount",
            "limit_field": "total",
            "message": "the refund is larger than the order total",
        },
        {
            "type": "required_status",
            **LOOKUP,
            "status_field": "status",
            "required_status": "delivered",
            "message": "only delivered orders can be refunded",
        },
        {
            "type": "time_window",
            **LOOKUP,
            "date_field": "delivered_at",
            "days": 30,
            "message": "the 30 day return window has passed",
        },
    ],
    "change_address": [
        {"type": "owned_record", **LOOKUP, "message": "that order does not belong to this customer"}
    ],
}


def upgrade() -> None:
    op.add_column(
        "tool_definitions",
        sa.Column(
            "policies",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    # Tenants created before this migration relied on the policies that were coded by tool name.
    for name, specs in LEGACY.items():
        op.execute(
            sa.text(
                "UPDATE tool_definitions SET policies = CAST(:specs AS jsonb) WHERE name = :name"
            ).bindparams(specs=json.dumps(specs), name=name)
        )


def downgrade() -> None:
    op.drop_column("tool_definitions", "policies")
