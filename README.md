# Partizan_bot

**Partizan Bot** — autonomous AI Growth Operator for internet-native products.

The core promise is simple:

> Give the system a product, a business goal, a budget and a target CAC — Partizan Bot researches audiences, finds concrete distribution opportunities, generates growth experiments, helps execute them, measures outcomes and learns what to scale.

## Product direction

The full product vision, agent architecture, MVP scope and development roadmap are documented here:

- [Product Vision & Action Plan](docs/PRODUCT_PLAN.md)

## MVP distribution scope

The channel-first MVP focuses on:

- Telegram;
- Instagram;
- Reddit;
- TikTok.

The current backend loop is:

```text
Product + ICP
  → Audience Intelligence
  → Distribution Opportunities
  → platform-aware tactics
  → identity / policy / campaign slot
  → DistributionAction + Experiment
  → execution adapter
  → analytics / CAC / ROAS
  → Growth Manager
  → next portfolio
```

## North-star product principle

**Execution over recommendations.**

Partizan Bot should evolve from “here is what you could do” to **“here is what I found, what I launched, what worked and what I am doing next.”**

## Local development

Partizan uses Python 3.12, FastAPI, PostgreSQL, SQLAlchemy/Alembic and pytest.

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

Stop it with:

```bash
make runtime-down
```

Both the API and `paid-control-worker` are forced to use `RUNTIME_STORAGE=database` and the same container database URL. This is required because the worker is a separate process and must see the same persisted DistributionActions, execution receipts, provider-control snapshots and analytics state as the API.

The worker command is:

```bash
partizan-paid-control --interval-seconds 60
```

Set `PAID_CONTROL_INTERVAL_SECONDS` to change the recurring interval. The worker enforces a minimum interval and never creates spend authorization, increases campaign budgets, or restarts paused campaigns. It only invokes the existing Meta/TikTok provider-control sync paths, including their hard budget-cap and reconciliation safeguards.

### Provider secrets

Paid-provider tokens are environment secrets. A provider connection stores only the **name** of the environment variable containing the token. Put the actual secret into the deployment environment / `.env` under that exact name; never commit token values.

For example, if a Meta connection is configured with `access_token_env=META_ORACLE_ACCESS_TOKEN`, the runtime environment must contain `META_ORACLE_ACCESS_TOKEN=<secret>`.

### Paid-control operations

Operational endpoints include:

- `GET /v1/ops/paid-control/sweeps?limit=20` — recent autonomous sweep history;
- `GET /v1/ops/paid-control/reconciliation` — current provider incidents requiring reconciliation;
- `POST /v1/ops/paid-control/reconciliation/{action_id}/sync` — safe provider re-sync only.

The reconciliation endpoint cannot activate campaigns, increase budgets, or re-enable spend.

## Health

The API exposes:

- `GET /health`

The Docker API service uses this endpoint for its container healthcheck.
