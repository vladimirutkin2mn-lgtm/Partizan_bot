# Milestone 7 — Growth Manager

## Goal

Turn observed experiment economics into an explicit next action:

- `SCALE`;
- `CONTINUE`;
- `MODIFY`;
- `STOP`.

The first policy is intentionally deterministic and guardrail-driven. The system should not ask an
LLM to improvise whether real marketing spend should be increased or stopped.

## Flow

```text
RUNNING / FINISHED Experiment
        +
Analytics Loop metrics
        +
Product target CAC / total budget
        +
Growth Play test assumptions
        ↓
GrowthPolicy v1
        ↓
SCALE / CONTINUE / MODIFY / STOP
        ↓
explicit rationale + budget recommendation
        ↓
next hypothesis
        ↓
immutable Decision snapshot
        ↓
learning memory
```

## Policy v1

Policy version: `growth-policy-v1`.

### With a target CAC

The strongest scale signal requires at least 3 attributed paid users.

For that sample:

- CAC ≤ 80% of target → `SCALE`;
- CAC ≤ 110% of target → `CONTINUE`;
- CAC ≤ 150% of target → `MODIFY`;
- CAC > 150% of target → `STOP`.

With only 1–2 paid users, the policy avoids scaling. Good early CAC continues collecting signal;
moderately bad CAC triggers modification; materially bad CAC with enough spend triggers the loss
guardrail.

With zero paid users:

- reaching the play/test loss guardrail → `STOP`;
- 20+ visits with no signup → `MODIFY` the hook/CTA/landing message;
- 5+ signups with no paid user → `MODIFY` activation/offer/paywall;
- otherwise → `CONTINUE` until there is enough signal.

### Without a target CAC

The fallback uses observed return more conservatively:

- 3+ paid users and ROAS ≥ 1 → `SCALE`;
- some paid users → `CONTINUE`;
- enough spend with no paid users → `STOP`;
- 20+ visits with no paid result → `MODIFY`;
- otherwise → `CONTINUE`.

## Budget guardrail

Product-level remaining budget is calculated from the founder-provided product budget minus actual
spend across experiments.

If the product budget is exhausted, the current experiment receives `STOP` regardless of a positive
local signal. This is an execution guardrail, not a statement that the acquisition hypothesis is
bad.

Recommended incremental budget is returned only for `SCALE` and `CONTINUE`, and is capped by the
remaining product budget.

This milestone recommends allocation; it does not automatically spend additional money.

## Automatic next hypothesis

Every decision includes a next hypothesis:

### SCALE

Repeat the winning play/source type with a bounded incremental test.

### CONTINUE

Keep the current play stable until the minimum paid-user signal or test guardrail is reached.

### MODIFY

Change one bottleneck while keeping the rest of the experiment stable:

- visits but no signups → hook / CTA / landing message;
- signups but no paid → activation / offer / paywall;
- paid but high CAC → lower-cost partnership/offer structure;
- otherwise → change one major variable such as hook or offer.

### STOP

Retire the current channel/tactic and test the same ICP through a different source type.

## Decision snapshots

Decisions are immutable observations of a particular metrics/budget state.

Every new snapshot stores:

- action;
- rationale;
- policy version;
- observed metrics;
- remaining budget;
- recommended incremental budget;
- next hypothesis;
- input fingerprint;
- creation time.

Calling evaluation again without changing metrics/budget returns the existing decision with
`duplicate=true` rather than creating another history/memory record.

Once new events or spend arrive, a new snapshot can be created.

## Learning memory

Each non-duplicate decision appends a compact learning record containing:

- product and experiment IDs;
- channel source type;
- Growth Play template;
- decision action;
- observed CAC;
- paid-user count;
- revenue;
- concise result summary.

This is the first empirical data moat for later policy calibration: over time Partizan Bot can learn
which `product × ICP × source type × tactic` combinations actually deliver acceptable economics.

## API

Decision:

- `POST /v1/experiments/{experiment_id}/decision`.

History:

- `GET /v1/experiments/{experiment_id}/decisions`;
- `GET /v1/products/{product_id}/decisions`.

Learning memory:

- `GET /v1/products/{product_id}/learning-memory`.

## Persistence

Existing `decisions` remain the core action records. Additional immutable context is stored in:

### `decision_contexts`

- product ID;
- policy version;
- rationale;
- metrics snapshot;
- budget guardrails;
- next hypothesis;
- fingerprint.

### `growth_learning_memory`

- product / experiment / decision IDs;
- source type and tactic template;
- action;
- observed CAC / paid users / revenue;
- result summary.

Alembic migration: `20260807_0008_growth_manager.py`.

## Definition of Done

- Growth Manager evaluates actual experiment metrics;
- policy returns SCALE / CONTINUE / MODIFY / STOP with explicit rationale;
- target CAC and product budget are enforced as guardrails;
- scale decisions require a minimum conversion signal;
- each decision has a concrete next hypothesis;
- identical snapshots are idempotent;
- changed metrics create new decision history;
- learning memory captures empirical experiment outcomes;
- experiment/product history APIs are available;
- Ruff and pytest pass in CI.

## Next handoff

Milestone 8 / Dogfood should stop adding architecture and run the complete system on a real digital
product. The goal is to replace mock search/delivery paths where needed, launch a tightly bounded
real experiment and compare ICP/channel/play priors with actual conversion and CAC.
