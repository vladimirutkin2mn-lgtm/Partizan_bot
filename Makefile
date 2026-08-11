.PHONY: install dev infra-up infra-down migrate test lint runtime-up runtime-down runtime-logs worker-once

install:
	python -m pip install -e ".[dev]"

dev:
	uvicorn app.main:app --reload

infra-up:
	docker compose up -d postgres

infra-down:
	docker compose down

migrate:
	alembic upgrade head

test:
	pytest

lint:
	ruff check .

runtime-up:
	docker compose up -d --build postgres migrate api paid-control-worker

runtime-down:
	docker compose down

runtime-logs:
	docker compose logs -f api paid-control-worker

worker-once:
	partizan-paid-control --once
