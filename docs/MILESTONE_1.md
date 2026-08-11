# Milestone 1 — Product Brief & Clarification

## Goal

Turn a founder's free-text description into a confirmed `ProductProfile` without reading the product website.

## Flow

```text
free-text brief
    ↓
Product Intake Agent
    ↓
structured ProductAnalysis
    ↓
completeness + contradiction checks
    ↓
0–3 high-value clarification questions
    ↓
founder answers
    ↓
re-analysis
    ↓
DRAFT profile ready for confirmation
    ↓
explicit confirm
    ↓
CONFIRMED
```

## Product facts policy

The founder is the source of truth for product facts. Reference links are stored but are not crawled during product intake. Uncertain interpretations belong in `assumptions`; conflicting facts belong in `contradictions` and are surfaced as clarification questions.

## LLM implementation

`ProductIntakeAgent` uses the provider abstraction from `app/llm.py`.

- `LLM_PROVIDER=mock`: deterministic local fallback for development and tests.
- `LLM_PROVIDER=openai`: OpenAI Responses API with Pydantic Structured Outputs.
- model is configured through `LLM_MODEL`.

The service remains provider-independent.

## Confirmation semantics

`DRAFT` means the profile has enough information for strategy but has not been explicitly confirmed by the founder.

`NEEDS_CLARIFICATION` means there are open high-value questions.

`CONFIRMED` is required before the growth workflow can start.

## API

- `POST /v1/products` — submit a free-text brief.
- `GET /v1/products/{product_id}` — inspect the current structured profile.
- `POST /v1/products/{product_id}/clarifications` — answer one clarification.
- `POST /v1/products/{product_id}/confirm` — confirm the product profile.
- `POST /v1/products/{product_id}/mock-workflow` — only allowed for confirmed profiles.

## Validation

Tests cover structured extraction through a scripted LLM provider, assumption tracking, the maximum-three-question rule, contradiction handling, answer ingestion, explicit confirmation, and blocking the growth workflow until confirmation.
