#!/bin/sh
# One command from a fresh clone to a running demo: services, demo tenant, documents, tools.
set -eu

cd "$(dirname "$0")/.."

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker and run 'make demo' again." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi

if ! grep -Eq '^OPENAI_API_KEY=.{10,}' .env; then
  echo "Set OPENAI_API_KEY in .env (the demo calls OpenAI for embeddings and answers), then run 'make demo' again." >&2
  exit 1
fi

PASSWORD="${DEMO_ADMIN_PASSWORD:-demo-admin-password}"
export DEMO_ADMIN_PASSWORD="$PASSWORD"

docker compose up -d --build --wait api worker kargoshop-mock
docker compose --profile seed run --rm seed

cat <<MSG

Mabeyn is running.
  API            http://localhost:8000  (docs: http://localhost:8000/docs)
  Mock shop      http://localhost:9000
  Admin login    admin@kargoshop.example / $PASSWORD

Try a customer chat: see "Quick start" in README.md or docs/demo-scenarios.md.
MSG
