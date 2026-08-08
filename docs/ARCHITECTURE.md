# Partizan Bot — Architecture Notes

## Shape

Partizan Bot starts as a modular monolith. The goal is to preserve clear contracts between product intake, discovery, execution, analytics and decision-making without paying the operational cost of microservices.

```text
FastAPI
  |
  +-- Product Intake
  +-- Growth Workflow
  +-- LLM Provider abstraction
  +-- Job Queue abstraction
  |
PostgreSQL / Alembic
```

## Boundaries

### Product intake

Owns the transition from a user-written product brief to a confirmed `ProductProfile`.

### Growth workflow

Owns stage orchestration. In Milestone 0 it exposes only a mock sequence; later milestones replace each pending stage with real agents/services.

### LLM provider

All model calls must pass through `LLMProvider`. Business services should not depend directly on a specific vendor SDK.

### Job queue

Long-running work must depend on `JobQueue`, not on a specific queue backend. Milestone 0 uses an inline implementation; a durable backend can be swapped in when asynchronous discovery becomes real.

### Persistence

SQLAlchemy models and the initial Alembic migration define the long-lived domain contracts. The temporary in-memory product-intake service is not the persistence architecture; it only proves the API/state machine before Milestone 1.

## Principle

Prefer the smallest abstraction that makes the next milestone replaceable without rewriting the public API or core domain contracts.
