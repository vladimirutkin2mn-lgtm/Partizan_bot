# Milestone 0 — Foundation

Status: implemented in the foundation PR.

## Decisions

- Python 3.12 + FastAPI.
- PostgreSQL + SQLAlchemy async + Alembic.
- Modular monolith; no microservices.
- Product facts come from the user, not URL crawling.
- `ProductProfile` states: `DRAFT / NEEDS_CLARIFICATION / CONFIRMED`.
- Foundation uses a deterministic in-memory clarification service; LLM-assisted extraction belongs to Milestone 1.
- Background execution is behind a `JobQueue` protocol with an inline implementation for tests/MVP foundation.
- LLM access is behind `LLMProvider` with a mock provider.
- Structured JSON logging uses the standard library.

## Foundation API

- `GET /health`
- `POST /v1/products`
- `POST /v1/products/{product_id}/clarifications`
- `POST /v1/products/{product_id}/mock-workflow`

## Data contracts

Initial database schema contains:

- Product;
- ClarificationQuestion;
- ICP;
- ChannelOpportunity;
- GrowthPlay;
- Experiment;
- Decision.

## Validation

Local validation before publishing:

- Python source compilation: passed;
- pytest: 6 passed.

## Next milestone

Milestone 1 replaces the deterministic product-intake rules with LLM-assisted structured extraction, gap/contradiction detection, high-value clarification questions and persistent application services.
