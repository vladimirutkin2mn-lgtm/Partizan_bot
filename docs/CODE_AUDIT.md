# Repository-wide pre-launch code audit

Audit scope: the complete `Partizan_bot` repository, including FastAPI routes, browser workspace, runtime persistence, background workers, provider/execution boundaries, attribution/analytics, Growth Manager, migrations, production Compose/Caddy/deploy scripts and CI contracts.

## Confirmed bugs fixed

### Production control-plane authentication

The production auth boundary originally protected unsafe methods but left internal `GET /v1` reads public. The browser workspace also did not attach the operator key to its normal API requests, so the UI could render in production while core actions failed with `401`.

Fixed by making internal `/v1` production access fail closed by default and adding a runtime-only same-origin browser auth bootstrap. Provider/Event keys are not persisted in browser storage.

### Distribution event/spend idempotency race

The original durable idempotency path performed `get → put`, allowing concurrent requests with the same id to race. It now reserves ids atomically with `put_if_absent` and rejects conflicting reuse.

### Read route with a write side effect

`GET .../paid-campaign-spec` could lazily create the spec. Specs are already created during prepare/auto-prepare, so the GET route is now read-only.

### Readiness lifecycle fragility

The readiness probe reused an async SQLAlchemy engine across test/event-loop lifetimes. It now uses a short synchronous PostgreSQL `SELECT 1`, which matches the actual readiness contract and avoids async-loop coupling.

### Cross-process autonomous sweep race

The autonomous sweep originally had only a `threading.Lock`, which does not serialize the API process and autonomous worker process. Durable runtime now holds a PostgreSQL session advisory lock across the full controlled sweep. Contention fails closed; the recurring worker retries without counting a skipped attempt as success.

## Obsolete runtime retired

The repository used to expose two generations of growth execution simultaneously:

1. `ChannelHunter → GrowthPlay → ExecutionPackage → legacy analytics → legacy GrowthManager`;
2. the current durable distribution domain.

The browser workspace, generic growth runner, workers and current learning/economics loop use the distribution domain. The obsolete HTTP/runtime path, mock workflow/job scaffolding and product-specific Oracle compatibility runner are retired in the final cleanup slice.

Historical Alembic revisions and SQLAlchemy model metadata are intentionally retained to preserve database migration continuity.

## Verification boundary

A green repository CI proves the checked-in code paths, migrations, browser JavaScript contracts, development/production Compose configuration, Caddy configuration, isolated E2E sandbox and production image build.

It does **not** prove live third-party credentials, a real dedicated production host, DNS propagation, provider-account permissions or real acquisition performance. Those require the remaining production-host milestone and real dogfood.

## Audit completion criteria

The audit is complete only when the final cleanup PR is merged and the resulting `main` passes the full repository CI suite. At that point there are no known launch-blocking code defects from this audit; live infrastructure/provider validation remains a separate operational step.
