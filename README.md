# Mabeyn

A secure, multi-tenant agentic support layer between businesses and their customers.

Businesses connect their documents and APIs and embed a support agent in their site. The agent answers from the documents with citations, calls tools to get work done, waits for human approval on risky actions, remembers returning customers, and is guarded by an LLM firewall.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the plan and [docs/adr](docs/adr) for design decisions.

## Quick start

Requirements: Docker, [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up -d --build        # api, postgres (pgvector), redis; migrations run first
curl localhost:8000/health
```

Create a tenant and log in:

```bash
uv sync
uv run python -m mabeyn.cli create-tenant \
  --name "KargoShop" --slug kargoshop --admin-email admin@kargoshop.test
curl -X POST localhost:8000/auth/login \
  -H 'content-type: application/json' \
  -d '{"email": "admin@kargoshop.test", "password": "<your password>"}'
```

## Development

```bash
uv sync
docker compose up -d postgres redis
make migrate                        # or: uv run alembic upgrade head
make test lint typecheck
```

## Status

Phase 1 (foundation and tenant isolation) is in place: tenant-scoped schema with Row-Level Security, admin auth with refresh rotation, API keys and short-lived customer tokens.
