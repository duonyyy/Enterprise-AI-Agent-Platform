.PHONY: help dev-up dev-down lint test test-contracts migrate

help:
	@echo "Available commands:"
	@echo "  make dev-up          - Start Postgres, Qdrant, MinIO via docker-compose"
	@echo "  make dev-down        - Stop infrastructure services"
	@echo "  make lint            - Run ruff and pnpm lint"
	@echo "  make test            - Run all Python and TypeScript unit tests"
	@echo "  make test-contracts  - Validate JSON schemas and compatibility"
	@echo "  make migrate         - Run alembic database migrations"

dev-up:
	docker compose -f infra/compose/docker-compose.dev.yml up -d

dev-down:
	docker compose -f infra/compose/docker-compose.dev.yml down

lint:
	uv run ruff check .
	pnpm lint

test:
	uv run pytest tests/
	pnpm test

test-contracts:
	uv run pytest tests/contract/

migrate:
	uv run --package control-api alembic upgrade head
