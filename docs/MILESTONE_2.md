# Milestone 2 — ICP Engine

## Goal

Turn a founder-confirmed `ProductProfile` into a ranked set of actionable audience hypotheses
that the Channel Hunter can use next.

The ICP Engine is intentionally hypothesis-driven. It does not claim that generated segments are
proven market facts; later experiments must validate them.

## Flow

```text
CONFIRMED ProductProfile
        ↓
ICP Agent generates 12–20 distinct segment hypotheses
        ↓
7-dimensional scoring rubric (1–10 each)
        ↓
deterministic weighted score (10–100)
        ↓
near-duplicate clustering
        ↓
ranked ICP list + explanations
```

## Segmentation principle

Prefer segments based on:

- pain or desire;
- trigger / moment of need;
- behavior and context;
- purchase intent;
- alternatives currently used;
- repeat-use pattern;
- discoverability in online channels.

Avoid relying primarily on generic demographic segments such as `men 25–40` unless demographic
information materially changes the use case or acquisition strategy.

## Scoring rubric

| Dimension | Weight |
|---|---:|
| pain intensity | 20% |
| purchase intent | 20% |
| willingness to pay | 15% |
| ease of targeting | 15% |
| market size | 10% |
| competitive headroom | 10% |
| speed of validation | 10% |

The LLM supplies dimension estimates from 1 to 10. The application computes the final weighted
score deterministically. This makes ranking reproducible and gives us a future calibration point
against observed CAC, conversion and retention.

`competitive_headroom=10` means relatively attractive / underserved / differentiated;
`competitive_headroom=1` means highly saturated and difficult to win.

## Duplicate handling

A lightweight token-similarity pass clusters obvious near-duplicates after scoring. The highest
scoring segment remains canonical. Duplicate metadata is returned so we can inspect generation
quality rather than silently discarding model output.

The API still guarantees at least 10 ranked segments when the model output satisfies its schema.

## API

- `POST /v1/products/{product_id}/icps/generate`
- `GET /v1/products/{product_id}/icps`

Generation returns HTTP 409 until the ProductProfile is explicitly `CONFIRMED`.

## Persistence contract

The `ICP` model now includes:

- rank;
- desired outcome;
- alternatives;
- message hook;
- score breakdown;
- deterministic score explanation;
- rationale;
- duplicate metadata;
- created timestamp.

Alembic migration: `20260807_0003_icp_engine.py`.

## Local fallback

When `LLM_PROVIDER=mock`, a deterministic 12-segment fallback validates contracts, ranking,
clustering and the end-to-end API without requiring an external API key. It is not intended as
production-quality audience research.

## Definition of Done

- confirmed ProductProfile can generate at least 10 ranked ICPs;
- each ICP has pain, trigger, WTP hypothesis, alternatives and message hook;
- every ICP has a 7-dimension score breakdown;
- final score is calculated by code, not by the model;
- ranking is descending and explainable;
- near-duplicate segments are surfaced in clusters;
- unconfirmed products cannot start ICP generation;
- Ruff and pytest pass in CI.

## Next handoff

Milestone 3 / Channel Hunter should initially use the top 3 ICPs and search only a few source
classes. It should preserve the ICP ID and score so channel relevance can later be evaluated in
context rather than globally.
