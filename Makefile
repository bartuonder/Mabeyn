.PHONY: demo up down test lint typecheck migrate seed eval ablation experiments docs gate redteam widget-e2e admin-e2e demo-media firewall-eval memory-eval loadtest

demo:
	./scripts/demo.sh

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

gate:
	EVAL_CACHE_DIR=evals/gate_cache uv run python -m evals.gate

redteam:
	uv run python -m evals.runners.redteam_eval

widget-e2e:
	mkdir -p tests/e2e/shots
	docker run --rm -v "$(CURDIR)/tests/e2e:/work" -e SHOTS=/work/shots \
		-e ADMIN_PASSWORD=$${DEMO_ADMIN_PASSWORD:-demo-admin-password} \
		--add-host=host.docker.internal:host-gateway mcr.microsoft.com/playwright/python:v1.49.0-noble \
		sh -c "pip install -q playwright==1.49.0 && python /work/widget_e2e.py"

admin-e2e:
	mkdir -p tests/e2e/shots
	docker run --rm -v "$(CURDIR)/tests/e2e:/work" -e SHOTS=/work/shots \
		-e ADMIN_PASSWORD=$${DEMO_ADMIN_PASSWORD:-demo-admin-password} \
		--add-host=host.docker.internal:host-gateway mcr.microsoft.com/playwright/python:v1.49.0-noble \
		sh -c "pip install -q playwright==1.49.0 && python /work/admin_e2e.py"

demo-media:
	docker compose restart kargoshop-mock
	mkdir -p demo/out docs/media
	docker run --rm -v "$(CURDIR)/demo:/work" -e ADMIN_PASSWORD=$${DEMO_ADMIN_PASSWORD:-demo-admin-password} \
		--add-host=host.docker.internal:host-gateway mcr.microsoft.com/playwright/python:v1.49.0-noble \
		sh /work/make_media.sh
	cp demo/out/demo.mp4 demo/out/demo.gif docs/media/
	mkdir -p docs/media/screenshots && cp demo/out/screenshots/*.png docs/media/screenshots/

firewall-eval:
	uv run python -m evals.runners.firewall_eval --set dev --history

memory-eval:
	uv run python -m evals.runners.memory_eval

loadtest:
	uv run python -m loadtest.run
