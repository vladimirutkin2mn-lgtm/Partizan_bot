# Partizan_bot

**Partizan Bot** — autonomous AI Growth Operator for internet-native products.

The core promise is simple:

> Give the system a product, a business goal, a budget and a target CAC — Partizan Bot researches audiences, finds concrete distribution opportunities, generates growth experiments, helps execute them, measures outcomes and learns what to scale.

## Product direction

The full product vision, agent architecture, MVP scope and development roadmap are documented here:

- [Product Vision & Action Plan](docs/PRODUCT_PLAN.md)
- [Current Implementation Status](docs/CURRENT_STATUS.md) — what is already in `main`, what remains open, and the next order of work.
- [Repository Boundary](docs/REPOSITORY_BOUNDARY.md) — external products are dependencies, not implicit write targets.
- [Production Runtime](docs/PRODUCTION_RUNTIME.md)
- [Generic Growth Runner](docs/GENERIC_GROWTH_RUNNER.md)
- [Isolated Growth Sandbox](docs/GROWTH_SANDBOX.md)
- [Marketing Intelligence](docs/MARKETING_INTELLIGENCE.md) — pinned marketing methodology for product intake, ICPs, evidence-backed audience discovery, creative drafting and bounded outreach.

## MVP distribution scope

The channel-first MVP focuses on Telegram, Instagram, Reddit and TikTok.

The production runtime uses the current distribution domain end to end:

```text
Product + ICP
  → Audience Intelligence
  → Distribution Opportunities
  → platform-aware tactics
  → DistributionAction + Experiment
  → execution adapter
  → distribution analytics / CAC / ROAS
  → Distribution Growth Manager
  → learning memory
  → next portfolio
```

The earlier pre-distribution `ChannelHunter → GrowthPlay → ExecutionPackage → legacy analytics` HTTP loop was retired after the repository-wide pre-launch audit. Historical Alembic migrations and SQLAlchemy model metadata are intentionally retained for schema continuity.

## North-star product principle

**Execution over recommendations.**

Partizan Bot should evolve from “here is what you could do” to **“here is what I found, what I launched, what worked and what I am doing next.”**

## Browser workspace

The browser workspace is served directly by FastAPI at `/app`; `/` redirects there.

It uses the live Partizan API for the core flow and includes customer/operator surfaces for discovery, execution, results, conversion integration, integration readiness, product-specific integration code, autonomy, creative/publishing and bounded outreach.

The customer workspace intentionally does not expose sensitive provider secrets. Convenience state is kept only in the current browser tab through `sessionStorage`; operator/Event Key secrets remain server/deployment scoped.

## Generic product growth run

The product-agnostic acquisition CLI is:

```bash
partizan-growth-run \
  --product-id <CONFIRMED_PRODUCT_UUID> \
  --destination-url https://your-product.example
```

Or start from a free-text brief:

```bash
partizan-growth-run \
  --brief-file product.txt \
  --destination-url https://your-product.example
```

If Product Intake asks a material clarification, the runner stops rather than guessing and tells you the exact `--answer field=value` needed. Dry-run is the default. `--execute` invokes only the existing approval + execution-adapter boundary; paid actions still stop at `STAGED` and the runner cannot authorize/activate spend.

See [Generic Growth Runner](docs/GENERIC_GROWTH_RUNNER.md) and the Product Integration Kit in `/app` before enabling real conversion delivery.

## Isolated end-to-end proof

Before involving a real product, the entire internal economics/learning loop can be proven with synthetic data:

```bash
partizan-sandbox-run
```

The sandbox starts a separate localhost Partizan process with in-memory state and mock/unavailable providers, traverses Product → experiment → VISIT/SIGNUP/ACTIVATED/PAID → spend → CAC/ROAS → Growth Manager → learning → next portfolio, then destroys the child process. It refuses `APP_ENV=production` and does not inherit provider/database/operator secrets.

Sandbox output is always synthetic and must not be treated as real dogfood performance. See [Isolated Growth Sandbox](docs/GROWTH_SANDBOX.md).

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
 ┌────┼─────────────────────┐
 API  paid-control-worker   autonomous-growth-worker
 :8000 provider control     bounded growth sweeps
```

Start the complete stack:

```bash
cp .env.example .env
make runtime-up
make runtime-logs
```

Stop it with `make runtime-down`.

Production/runtime workers are forced to use `RUNTIME_STORAGE=database` and the same container database URL. Paid control never creates spend authorization, increases campaign budgets or restarts paused campaigns; autonomous growth remains bounded by its explicit mandates/delegations and execution-specific controls.

### Paid-control operations

Operational endpoints include:

- `GET /v1/ops/paid-control/sweeps?limit=20`
- `GET /v1/ops/paid-control/lifecycle/{action_id}`
- `GET /v1/ops/paid-control/audit`
- `GET /v1/ops/paid-control/reconciliation`
- `POST /v1/ops/paid-control/reconciliation/{action_id}/sync`

## Health

The API exposes:

- `GET /health` — compatibility liveness;
- `GET /health/live` — process liveness;
- `GET /health/ready` — PostgreSQL-backed readiness.
