# Milestone 4 — Growth Play Generator

## Goal

Turn evidence-backed `ICP × ChannelOpportunity` pairs into concrete, measurable acquisition
experiments that can be reviewed and handed to the Execution Assistant.

## Flow

```text
Confirmed ProductProfile
        +
ranked ICPs
        +
30–60 ChannelOpportunity objects
        ↓
Growth Play Agent / template fallback
        ↓
20–30 executable experiment drafts
        ↓
deterministic priority scoring
        ↓
ranked PROPOSED plays
        ↓
user APPROVED / REJECTED
```

## Growth Play contract

Every play contains:

- exact ICP and ChannelOpportunity references;
- tactic template;
- acquisition hypothesis;
- offer;
- execution steps;
- success metric;
- expected result hypothesis;
- test cost range;
- effort estimate;
- time to signal;
- kill criteria;
- scale criteria;
- score breakdown and explanation;
- approval status.

The model may only reference channel IDs supplied by Channel Hunter. Invalid model-generated
channel IDs are discarded and deterministic templates fill the portfolio back to the minimum size.

## Initial play library

### Communities

- `community_value_post` — value-first native contribution with contextual CTA when permitted;
- `community_partnership` — moderator/owner partnership, resource, AMA or tracked member offer.

### Creators

- `creator_seeding` — product access + tracked referral for an authentic use case/review;
- `creator_affiliate` — CPA/revenue-share test;
- `creator_sponsored_test` — small fixed-budget native integration.

### Newsletters / niche sites

- `newsletter_sponsorship` — small tracked placement;
- `newsletter_affiliate` — CPA/revenue-share partnership;
- `content_partnership` — expert/resource/co-created content with tracked CTA.

The library is a starting prior, not a hard-coded final strategy. The production LLM can generate
more context-specific plays within the same executable contract.

## Safety / execution boundaries

Growth Play generation explicitly excludes:

- spam;
- fake accounts;
- fake reviews;
- fake engagement;
- impersonation;
- ban evasion;
- bypassing platform/community restrictions.

Community tactics must be value-first and compatible with community rules. Public posting,
outbound communication and spend are not automatically executed in this milestone; they require
approval and are handed to the next execution layer.

## Priority score

The LLM estimates four dimensions from 1 to 10. Application code computes the final 10–100 score:

| Dimension | Weight |
|---|---:|
| expected impact | 35% |
| confidence | 25% |
| cost efficiency | 20% |
| speed to signal | 20% |

This keeps prioritization reproducible and gives us a future calibration surface once real CAC and
conversion data exist.

## Budget guardrail

Deterministic fallback templates cap their maximum test cost at the founder-provided product budget.
This is only an experiment estimate; actual spend remains under the Execution Assistant / user
approval flow.

## Approval states

- `PROPOSED` — generated and ranked, no execution permission;
- `APPROVED` — user selected the play for execution preparation;
- `REJECTED` — user rejected the play.

## API

- `POST /v1/products/{product_id}/growth-plays/generate`;
- `GET /v1/products/{product_id}/growth-plays`;
- `POST /v1/products/{product_id}/growth-plays/{play_id}/approval`.

Generation requires both ICP and Channel Hunter results.

## Persistence contract

`GrowthPlay` now includes rank, template, status, offer, execution plan, success metric, cost range,
effort, time-to-signal, expected result, kill/scale criteria, deterministic priority, score
breakdown/explanation, rationale and timestamp.

Alembic migration: `20260807_0005_growth_play_generator.py`.

## Definition of Done

- at least 20 concrete Growth Plays are produced for discovered opportunities;
- every play references a real ChannelOpportunity;
- each play contains an executable plan and measurable decision criteria;
- final priority is calculated by code and sorted descending;
- the portfolio covers multiple source classes in deterministic fallback;
- test costs respect the product budget guardrail;
- approval state can be changed and retrieved;
- Ruff and pytest pass in CI.

## Next handoff

Milestone 5 / Execution Assistant should take only `APPROVED` Growth Plays and turn them into
execution packages: outreach/contact queue, personalized message or placement brief, tracking links,
user review/edit and an explicit `Approve & Run` transition. The first execution surface should
remain outreach/partnership-oriented rather than trying to become a full ad manager.
