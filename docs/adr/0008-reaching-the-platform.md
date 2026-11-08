# ADR-0008: How browsers, admins and background jobs reach the platform

- Status: accepted
- Date: 2026-11-07

## Context

The assistant came first. Putting people in front of it adds new ways to reach tenant data: a customer in a browser, a tenant admin in a panel, a tenant system that wants to hear about approvals. Four earlier decisions had been left open on purpose: the checkpoint and memory tables had no row-level security (ADR-0001, ADR-0006), argument policies existed only for the demo shop's tool names (ADR-0003), nobody was told about a pending approval (ADR-0002), and a widget needed keys that are safe in a page (ADR-0005).

## Decision

- **Row-level security reaches the LangGraph tables through the connection, not through a column.** The checkpoint tables key rows by a thread id of the form `<tenant>:<conversation>` and the memory store by a dotted namespace that starts with the tenant, so a policy compares that first part with `app.tenant_id`. The pools behind both use a `TenantPool` that sets the setting from a context variable (`tenant_scope`) every time a connection is borrowed and clears it on return. Outside a scope the setting is empty and every row is hidden, so a forgotten scope fails closed instead of leaking. Two details are easy to get wrong: the store's `aput` and `asearch` hand their work to a background task that does not inherit the context variable, so `MemoryStore` calls `abatch`, which runs in the calling task; and the cross-tenant TTL sweep became a narrow `SECURITY DEFINER` function, because the policy hides other tenants' expired rows from a plain `DELETE`.
- **Argument policies are data on the tool.** A tool definition carries a list of policies (`owned_record`, `max_amount`, `required_status`, `time_window`). Each names a read tool of the tenant, fetches the real record with the customer's injected identity and checks it, so the demo shop's refund rules (own order, amount, delivered, 30 days) are four entries in its seed file and any tenant can write the same for its own tools. A policy whose lookup tool is missing or unreachable blocks the call. Creating a tool validates the policies against the tenant's real tools and arguments.
- **Browsers hold no secret and no cookie reaches the API.** The widget is one script served by the API. An anonymous visitor uses a publishable key, which only opens anonymous sessions and checks the page's origin. A signed-in customer gets a session that the tenant's own server opens with a secret key (the demo storefront shows how), so the secret key never reaches a browser. Because every browser call carries a bearer token or a key and never a cookie, the API allows any origin (`CORS_ALLOW_ORIGINS` narrows it) and cross-site request forgery has nothing to ride on. The admin panel goes further: it calls the API only from its own server, keeps the admin's tokens in httpOnly cookies and renews an expired access token with the refresh token before a page renders.
- **Approvals are announced through a signed webhook.** A tenant sets a URL. A worker job (every minute) sends each new pending approval as an `approval.pending` JSON event signed with HMAC-SHA256 (`X-Mabeyn-Signature`, secret shown once). The event carries ids, the tool, a one-line summary and the expiry, no customer data. The URL must resolve to a public host, delivery is retried on the next runs up to five times, and approvals that were already pending when the webhook was set are not announced. Delivery state lives in the approval row (`notified_at`, `notify_attempts`), and the worker finds work through a function that only joins pending approvals with their tenant's webhook.

## Alternatives considered

- **A tenant column on the LangGraph tables.** Cleaner policies, but the tables belong to the library: its migrations would fight ours and every upgrade would need a patch.
- **A schema or database per tenant.** Strongest isolation, much heavier to operate, and it throws away what the existing policies already give.
- **Keeping the namespace as the only protection.** What the system did until now. It holds as long as every caller builds the namespace correctly; the policy makes the database enforce it too.
- **Policies as code per tool name, or as tenant-supplied scripts.** The first does not scale past the demo shop; the second needs a sandbox for little gain over four checks that cover refunds, address changes and similar.
- **Admin tokens in the browser (local storage) with CORS for the panel.** Simpler, but any script on the page could read them. Server-side calls and httpOnly cookies cost one extra hop.
- **Queueing the notification when the approval is created.** Lower delay, but a lost job loses the notification; a row that records "not yet announced" survives restarts and is retried.
- **E-mail or chat integrations first.** A signed webhook lets the tenant connect any of them without the platform storing their credentials.

## Consequences

- The policies on the LangGraph tables rely on the thread id and namespace formats. Any new writer must build them with `TurnContext.thread_id` and `namespace()`; a writer that bypasses them is hidden from the readers (a test covers foreign writes).
- Each borrowed connection runs two extra statements. Measured on the local database that is 0.36 ms instead of 0.12 ms, and a turn leaves a median of 87 checkpoint rows, so the cost is far below the noise of a model call (three load runs, see the README).
- Argument policies cover one record per call: a lookup, then equality, amount and date checks. Rules that need several records or business logic are not expressible.
- A webhook can arrive up to a minute late and more than once if the receiver answers after the job gave up; receivers should treat `approval_id` as the idempotency key. The signing secret is stored in the database in plain text because the platform has to compute the signature.
- The widget and the storefront are a reference integration, not a hardened product: the demo storefront creates its API keys with the admin password it is given.
