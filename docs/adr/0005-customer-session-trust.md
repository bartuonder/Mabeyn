# ADR-0005: Who vouches for the customer identity?

- Status: proposed (to be implemented with the widget)
- Date: 2026-11-01

## Context

`POST /sessions` takes a tenant API key and a free-text `customer_id` and returns a short-lived customer token. The platform then injects that `customer_id` into every tool call, and the shop backend trusts it. Anyone who holds the API key can therefore ask for a session as any customer and read that customer's orders with `get_order_status`.

Row-Level Security separates tenants from each other; it does not separate the customers of one tenant. The planned chat widget is a script on the tenant's public site, so a key embedded in it is visible to every visitor.

## Decision

Split the keys by what they are allowed to vouch for. Implementation is deferred to the widget phase.

- **Publishable keys** (`mbn_pub_…`) are safe in a browser. They only open anonymous sessions: the platform picks the `customer_id` (`anon:<random>`), and the session can use the knowledge agent and escalation but no customer-scoped tool. They can be limited to a list of allowed origins.
- **Secret keys** (`mbn_sec_…`) stay on the tenant's server. They may open a session for a named `customer_id`. The tenant's backend authenticates its own user, requests the session, and hands only the short-lived customer token to the browser.
- Tools get a `requires_identity` flag (default true). Anonymous sessions never see those tools, and the executor refuses to call them without a verified customer.
- Session creation is rate limited per key and per client address.

## Alternatives considered

- **Customer assertions signed by the tenant**: the tenant backend signs a short JWT with a per-tenant secret and the widget forwards it. Equivalent trust and no extra server call, but it adds key management for tenants. It can be added later on top of secret keys.
- **Keeping one key kind and documenting that it must stay secret**: cannot work for a widget that runs in the browser.

## Consequences

- Breaking change to `POST /sessions` and the `ApiKey` model (a `kind` column, a migration, hashed prefixes that show the kind).
- The widget snippet, the admin panel's key page and the demo scripts change accordingly.
- Until this is implemented, API keys must be treated as secrets and the system must not be offered to untrusted browsers.
