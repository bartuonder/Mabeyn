.PHONY: up down test lint typecheck migrate

up:
	docker compose up -d --build

down:
	docker compose down

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy mabeyn apps evals

migrate:
	uv run alembic upgrade head
