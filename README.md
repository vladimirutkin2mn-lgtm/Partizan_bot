# Partizan_bot

**Partizan Bot** — autonomous AI Growth Operator for internet-native products.

The core promise is simple:

> Give the system a product, a business goal, a budget and a target CAC — Partizan Bot researches audiences, finds concrete distribution opportunities, generates growth experiments, helps execute them, measures outcomes and learns what to scale.

## Product direction

The full product vision, agent architecture, MVP scope and development roadmap are documented here:

- [Product Vision & Action Plan](docs/PRODUCT_PLAN.md)

## Initial focus

The first version is intentionally narrow and targets:

- Telegram bots;
- AI apps;
- SaaS;
- mobile apps;
- browser extensions;
- digital subscription products.

The first end-to-end loop we want to prove is:

```text
Product
  → ICP discovery
  → concrete channel discovery
  → Growth Plays
  → experiment
  → metrics
  → Scale / Modify / Stop
  → next experiment
```

## North-star product principle

**Execution over recommendations.**

Partizan Bot should evolve from “here is what you could do” to **“here is what I found, what I launched, what worked and what I am doing next.”**

## Local development

Milestone 0 uses Python 3.12, FastAPI, PostgreSQL, SQLAlchemy/Alembic and pytest.

```bash
cp .env.example .env
make install
make infra-up
make migrate
make dev
```

Useful commands:

```bash
make test
make lint
```

Initial API contracts:

- `GET /health`
- `POST /v1/products`
- `POST /v1/products/{product_id}/clarifications`
- `POST /v1/products/{product_id}/mock-workflow`

The product-intake service is intentionally in-memory in Milestone 0. Its purpose is to prove the
`DRAFT / NEEDS_CLARIFICATION / CONFIRMED` contracts and the mock end-to-end workflow before
Milestone 1 adds LLM-assisted extraction, clarification quality and persistent application services.
