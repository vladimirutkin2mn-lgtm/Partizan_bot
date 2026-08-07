# Milestone 6 — Analytics Loop

## Goal

Close the measurement loop for launched acquisition experiments: ingest outcomes, attribute them to
an experiment, track spend and calculate normalized acquisition economics.

## Flow

```text
Experiment RUNNING
        ↓
VISIT / SIGNUP / ACTIVATED / PAID events
        +
spend records
        ↓
attribution resolution
        ↓
idempotent raw ledgers
        ↓
normalized experiment metrics
        ↓
CAC / conversion / revenue / ROAS
        ↓
product experiment dashboard
```

## Attribution

An event can identify its experiment using one or more of:

1. `experiment_id` — direct server-side attribution;
2. `referral_token` — the `partizan_<token>` value created by Execution Assistant;
3. `utm_content` — the Growth Play UUID embedded in the tracking URL.

If multiple identifiers are supplied, all of them must resolve to the same experiment. Conflicting
identifiers are rejected instead of silently selecting one attribution path.

Events are accepted only when the resolved experiment is `RUNNING` or `FINISHED`.

## Event contract

Supported normalized acquisition events:

- `VISIT`;
- `SIGNUP`;
- `ACTIVATED`;
- `PAID`.

A `PAID` event can carry `revenue`. Revenue on other event types is rejected as a validation error.

`actor_id` is optional. When present, signup/activation/paid-user metrics count unique actors. When
it is absent, the event ID acts as the conversion identity.

`transactions` remains the raw count of PAID events, so repeat purchases by one actor increase
transactions and revenue without incorrectly increasing `paid_users`.

## Idempotency

Both acquisition events and spend records have caller-provided/default UUIDs:

- `event_id`;
- `spend_id`.

Retrying the exact same record returns `duplicate=true` and does not change metrics. Reusing an ID
with different material data is rejected.

This makes the ingestion endpoints safe for webhook retry behavior.

## Spend

Spend is an additive ledger rather than a mutable total. Every record has an amount, timestamp and
optional properties. The dashboard calculates total spend by summing the raw ledger.

## Metrics

Per experiment:

- spend;
- visits;
- signups;
- activated users;
- paid users;
- transactions;
- revenue;
- visit → signup conversion;
- signup → paid conversion;
- CAC = spend / paid users;
- ROAS = revenue / spend;
- revenue per paid user.

Metrics with a zero denominator return `null` rather than an artificial zero.

Product dashboard aggregates:

- experiment count;
- total spend;
- total attributed paid users;
- total revenue;
- blended CAC;
- blended ROAS;
- per-experiment analytics sorted with measurable lower-CAC experiments first.

## API

Event ingestion:

- `POST /v1/analytics/events`.

Spend ingestion:

- `POST /v1/experiments/{experiment_id}/spend`.

Dashboards:

- `GET /v1/experiments/{experiment_id}/analytics`;
- `GET /v1/products/{product_id}/analytics`.

## Persistence

Raw facts are persisted; derived CAC/ROAS are not stored as authoritative values.

Tables:

### `analytics_events`

- id / idempotency key;
- experiment ID;
- event type;
- actor ID;
- revenue;
- attribution method;
- properties;
- occurred / received timestamps.

### `experiment_spend`

- id / idempotency key;
- experiment ID;
- amount;
- properties;
- occurred / received timestamps.

Alembic migration: `20260807_0007_analytics_loop.py`.

## Definition of Done

- launched experiments accept attributable acquisition events;
- direct, referral and UTM attribution work;
- conflicting attribution is rejected;
- webhook retries do not double-count events;
- spend retries do not double-count cost;
- signup, paid users and revenue are normalized;
- CAC and ROAS are calculated from actual facts;
- experiment and product dashboards are available;
- events cannot be attached to an experiment that has not launched;
- Ruff and pytest pass in CI.

## Next handoff

Milestone 7 / Growth Manager can now operate on actual experiment economics instead of estimates.
Its first policy should remain deterministic and guardrail-driven: compare observed metrics to the
Growth Play kill/scale criteria and product CAC target, then choose `SCALE / CONTINUE / MODIFY /
STOP` with an explicit rationale before introducing more advanced learning policies.
