.PHONY: up down test lint typecheck migrate seed eval ablation experiments docs

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

seed:
	uv run python -m mabeyn.cli seed-kargoshop

eval:
	uv run python -m evals.runners.rag_eval --name manual --config full

ablation:
	uv run python -m evals.runners.ablation

experiments:
	uv run python -m evals.runners.experiments

docs:
	uv run python -m mabeyn.docs_sync
