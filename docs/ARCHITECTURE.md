# Partizan Bot — Architecture

## Shape

Partizan is a modular monolith: one application image, one PostgreSQL database, one FastAPI control/data plane, and bounded background workers. Domain boundaries stay explicit so execution, provider adapters and learning can evolve without introducing microservice operational cost prematurely.

```text
                         ┌──────────────────────┐
Product brief ──────────▶│ Product Intake + ICP │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │ Audience Intelligence │
                         │ + Distribution Plays │
                         └──────────┬───────────┘
                                    │
                                    ▼
                         ┌──────────────────────┐
                         │ Distribution Action  │
                         │ + Experiment         │
                         └──────────┬───────────┘
                                    │
                  ┌─────────────────┼──────────────────┐
                  ▼                 ▼                  ▼
             paid/provider      owned/organic       outreach
               adapters            adapters          boundary
                  └─────────────────┼──────────────────┘
                                    ▼
                         ┌──────────────────────┐
                         │ Attribution/events   │
                         │ spend/CAC/ROAS       │
                         └──────────┬───────────┘
                                    ▼
                         ┌──────────────────────┐
                         │ Growth Manager       │
                         │ learning + portfolio │
                         └──────────────────────┘
```

## Runtime processes

Production uses the same image for separate responsibilities:

- **API** — browser workspace, operator control plane, Product Event Key data plane, tracking and health endpoints.
- **paid-control-worker** — provider reconciliation and bounded paid-control sweeps.
- **autonomous-growth-worker** — bounded autonomous growth sweeps under explicit Growth Mandates.
- **PostgreSQL** — durable runtime state and migration-backed schema.
- **Caddy edge** — optional repository-owned HTTPS reverse proxy on a dedicated Partizan host.

Workers share durable state with the API. Autonomous sweeps are serialized across processes with a PostgreSQL advisory lock; worker health is verified by database-backed heartbeats rather than container presence alone.

## Security boundaries

Production internal `/v1` control-plane reads and writes require the operator key by default. Explicit public surfaces are limited to health/web/tracking, opaque public creative blobs and Product Event Key conversion verification/ingestion endpoints.

The browser operator key is runtime-only and is never persisted in `localStorage` or `sessionStorage`. Provider credentials remain server/deployment scoped.

## Persistence

Current domain runtime state is stored through `RuntimeStateStore`; production requires the database implementation. Product Intake, ICP and distribution services hydrate from durable snapshots after restart.

Historical Alembic revisions and SQLAlchemy model metadata from pre-distribution milestones remain intentionally present. They are database history, not an active second runtime architecture, and must not be rewritten or deleted merely because the corresponding early HTTP flow has been retired.

## Provider boundary

External mutations happen only through explicit execution/provider adapters and dedicated reconciliation/control services. Paid activation, organic publishing and outreach each retain their own fail-closed safety contracts. Ambiguous external results require reconciliation rather than blind retry.

## Product integration

External products integrate through Partizan's tracking and Product Event Key contracts. Partizan must not modify another product repository implicitly; external codebases and infrastructure are dependencies unless their owner explicitly authorizes changes.

## Principle

**Execution over recommendations, but mutation only through an explicit bounded contract.**
