"""enable row level security

Revision ID: 0003
Revises: 0002
"""

from alembic import op
from sqlalchemy.engine import make_url

from mabeyn.core.config import get_settings

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "admin_users",
    "refresh_tokens",
    "api_keys",
    "documents",
    "chunks",
    "conversations",
    "messages",
    "tool_definitions",
    "tool_calls",
    "approvals",
    "security_events",
]

TENANT_SETTING = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _app_role() -> tuple[str, str]:
    url = make_url(get_settings().database_url)
    if not (url.username and url.password):
        raise RuntimeError("DATABASE_URL must include a username and password")
    return url.username, url.password


def upgrade() -> None:
    role, password = _app_role()
    quoted_role = op.get_bind().dialect.identifier_preparer.quote(role)
    literal_password = password.replace("'", "''")

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {quoted_role} LOGIN PASSWORD '{literal_password}'
                    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
            END IF;
        END
        $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {quoted_role}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {quoted_role}"
    )
    op.execute(f"REVOKE ALL ON alembic_version FROM {quoted_role}")
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {quoted_role}"
    )

    # Tenants are provisioned with the owner connection only.
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON tenants FROM {quoted_role}")
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON tenants USING (id = {TENANT_SETTING})")

    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {TENANT_SETTING}) WITH CHECK (tenant_id = {TENANT_SETTING})"
        )

    # Pre-auth lookups run before the tenant is known, so they bypass RLS in a
    # narrow, read-only function owned by the migration role.
    op.execute(
        """
        CREATE FUNCTION auth_get_admin_by_email(p_email text)
        RETURNS SETOF admin_users
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
        AS $$ SELECT * FROM admin_users WHERE email = p_email AND is_active $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION auth_get_api_key(p_hash text)
        RETURNS SETOF api_keys
        LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
        AS $$ SELECT * FROM api_keys WHERE key_hash = p_hash AND revoked_at IS NULL $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION auth_get_admin_by_email(text) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION auth_get_api_key(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth_get_admin_by_email(text) TO {quoted_role}")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth_get_api_key(text) TO {quoted_role}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_get_api_key(text)")
    op.execute("DROP FUNCTION IF EXISTS auth_get_admin_by_email(text)")
    for table in [*TENANT_TABLES, "tenants"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
