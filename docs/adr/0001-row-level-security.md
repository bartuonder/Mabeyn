# ADR-0001: Why Row-Level Security?

- Status: accepted
- Date: 2026-10-31

## Context

Mabeyn serves many businesses from one deployment. A tenant must never read or write another tenant's documents, conversations, tools or audit data. Enforcing this with `WHERE tenant_id = ...` in application code fails silently the first time a query forgets the filter.

## Decision

Isolation is enforced by PostgreSQL Row-Level Security.

- Every tenant-scoped table has a `tenant_id` column and a `tenant_isolation` policy with `USING` and `WITH CHECK` on `tenant_id = app.tenant_id`.
- Each request runs in one transaction that starts with `set_config('app.tenant_id', ..., true)`. The setting is transaction-local, so it cannot leak through the connection pool.
- The API connects as `mabeyn_app`, a role without superuser or `BYPASSRLS`. Migrations and tenant provisioning use the owner role.
- `tenants` is readable only for the current tenant and is read-only for the app role.
- Two narrow `SECURITY DEFINER` functions (`auth_get_admin_by_email`, `auth_get_api_key`) cover the only lookups that happen before the tenant is known. They are read-only and return no more than the row being authenticated.
- Without tenant context, policies evaluate to no rows instead of raising.

## Consequences

- A forgotten filter returns nothing instead of leaking data; isolation tests exercise the database directly, not only the API.
- Every tenant-scoped query must run through `tenant_session`; a new table needs a policy in its migration.
- The owner role bypasses RLS by design, so it must never be used at runtime.
- Background workers must set the tenant context the same way.
- Slight per-query overhead from policy evaluation, mitigated by the `tenant_id` index on every table.
