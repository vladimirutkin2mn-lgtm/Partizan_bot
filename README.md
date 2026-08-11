# Partizan_bot

**Partizan Bot** — autonomous AI Growth Operator for internet-native products.

The core promise is simple:

> Give the system a product, a business goal, a budget and a target CAC — Partizan Bot researches audiences, finds concrete distribution opportunities, generates growth experiments, helps execute them, measures outcomes and learns what to scale.

## Product direction

The full product vision, agent architecture, MVP scope and development roadmap are documented here:

- [Product Vision & Action Plan](docs/PRODUCT_PLAN.md)

## MVP distribution scope

The channel-first MVP focuses on Telegram, Instagram, Reddit and TikTok.

The current backend loop is:

```text
Product + ICP
  → Audience Intelligence
  → Distribution Opportunities
  → platform-aware tactics
  → DistributionAction + Experiment
  → execution adapter
  → analytics / CAC / ROAS
  → Growth Manager
  → next portfolio
```

## North-star product principle

**Execution over recommendations.**

Partizan Bot should evolve from “here is what you could do” to **“here is what I found, what I launched, what worked and what I am doing next.”**

## Dogfooding workspace

The first browser workspace is served directly by FastAPI at `/app`; `/` redirects there.

It uses the live Partizan API for the core discovery flow:

```text
Product brief
  → clarifications and ProductProfile confirmation
  → ranked ICPs
  → Audience Distribution Map
  → Telegram / Instagram / Reddit / TikTok opportunities
  → ranked Distribution Plays
```

The customer workspace intentionally does not expose sensitive operator mutations. Convenience state is kept only in the current browser tab through `sessionStorage`.

## Local development

Partizan uses Python 3.12, FastAPI, PostgreSQL, SQLAlchemy/Alembic and pytest.

```bash
cp .env.example .env
make install
make infra-up
make migrate
make dev
```

Open `http://localhost:8000/app`.

Useful commands:

```bash
make test
make lint
make worker-once
```

`make infra-up` intentionally starts only PostgreSQL so normal local development can continue to run the API directly with `make dev`.

## Production-like runtime

The repository ships one application image and runs separate processes from it:

```text
postgres healthy
      ↓
migrate (alembic upgrade head)
      ↓
 ┌────┴───────────────┐
 API              paid-control-worker
 :8000             recurring provider sync
```

Start the complete stack:

```bash
cp .env.example .env
make runtime-up
make runtime-logs
```

Stop it with `make runtime-down`.

Both the API and `paid-control-worker` are forced to use `RUNTIME_STORAGE=database` and the same container database URL. The worker never creates spend authorization, increases campaign budgets, or restarts paused campaigns; it only invokes existing provider-control sync and hard-stop/reconciliation paths.

### Paid-control operations

Operational endpoints include:

- `GET /v1/ops/paid-control/sweeps?limit=20`
- `GET /v1/ops/paid-control/lifecycle/{action_id}`
- `GET /v1/ops/paid-control/audit`
- `GET /v1/ops/paid-control/reconciliation`
- `POST /v1/ops/paid-control/reconciliation/{action_id}/sync`

## Health

The API exposes `GET /health`, which is also used by the Docker API service healthcheck.
