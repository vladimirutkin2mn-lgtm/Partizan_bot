.PHONY: install dev infra-up infra-down migrate test lint

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
