# Partizan Bot — Current Implementation Status

This document is the current source of truth for implementation progress. `PRODUCT_PLAN.md` remains the long-form product vision and historical roadmap.

## Completed foundation

The original Milestones 0–7 are implemented and their GitHub issues are closed as completed:

- Milestone 0 — Foundation (#2)
- Milestone 1 — Product Brief & Clarification (#3)
- Milestone 2 — ICP Engine (#4)
- Milestone 3 — Channel Hunter (#5)
- Milestone 4 — Growth Play Generator (#6)
- Milestone 5 — Execution Assistant (#7)
- Milestone 6 — Analytics Loop (#8)
- Milestone 7 — Growth Manager (#9)

The current `main` loop is:

```text
Product brief
  -> ProductProfile + clarifications
  -> ranked ICPs
  -> concrete distribution opportunities
  -> platform-aware Distribution Plays
  -> DistributionAction + Experiment
  -> permissioned execution adapters
  -> VISIT / SIGNUP / ACTIVATED / PAID attribution
  -> spend / CAC / ROAS
  -> SCALE / CONTINUE / MODIFY / STOP
  -> learning memory + next portfolio
```

## Execution capabilities already in `main`

Current production-oriented capabilities include:

- Telegram permissioned execution for explicitly configured and allowlisted owned targets;
- Meta Ads staged creation, exact-budget activation authorization, activation, provider sync, hard stop and reconciliation;
- TikTok Ads staged creation, exact-budget activation authorization, activation, provider sync, hard stop and reconciliation;
- paid-control worker that may sync/pause/reconcile but cannot create new spend authorization, increase budgets or restart paused spend;
- first-click referral tracking plus server-to-server conversion ingestion;
- Results & Learning workspace with experiment economics and Growth Manager decisions;
- creative generation/finalization flows and permissioned TikTok owned publishing;
- evidence-backed creator/partner OutreachTarget, suppression, personalized OutreachBrief, owned SMTP readiness, explicit one-message authorization and restart-safe send ledger;
- bounded Outreach Policy with autonomous target/draft preparation that stops at approval when no narrower execution delegation exists.

## Current active milestone — #109

**Milestone 12 — Autonomous Creator & Partner Outreach** is the current implementation focus.

Already in `main`:

1. evidence-backed OutreachTarget and suppression;
2. truthful personalized OutreachBrief + offer + referral tracking;
3. owned SMTP sender readiness and restart-safe one-message execution;
4. bounded Outreach Policy and autonomous preparation.

Still required to close #109:

1. Founder Outreach workspace in `/app` showing target evidence, ICP overlap, offer/message, sender/policy state, delivery/reconciliation state and conversions;
2. explicit milestone sign-off for the already implemented analytics -> Growth Manager -> learning integration;
3. any autonomous execution extension must remain separately delegated, low-volume, zero-follow-up and fail closed on ambiguous provider outcomes.

PR #114 contains a draft implementation of the narrowly delegated bounded autonomous send slice. It is intentionally not part of `main` until that execution boundary is approved for merge.

The current test suite already covers executed experiment attribution, CAC/ROAS, Growth Manager decisions, learning memory, next-portfolio changes, first-click redirect VISIT tracking, and the one-message outreach send transition to `RUNNING`. Issue #115 remains the bookkeeping item for explicit validation closure rather than a missing analytics capability.

## Real-product dogfood — #10

**Milestone 8 — Dogfood on a real product** remains open by design. Code readiness is not enough to close it.

Chosen product: `Bot_globa / Oracle`.

Business assumptions for the first acquisition loop:

- subscription: `$6.90/month`;
- initial acquisition budget: `$1,000`;
- target max CAC: `$12` per paid subscriber;
- initial audience: English-speaking adults roughly 20–40 interested in astrology, relationships and self-reflection;
- initial distribution scope: Telegram / Instagram / Reddit / TikTok.

### Oracle runtime status

The Oracle backend is already deployed to the shared production host through the Bot_globa GitHub Actions production environment. Bot Globa CI run #44 for commit `3d18a118d2322cd282758daa299089280de5a44c` completed its production deploy job successfully on 2026-08-13.

That production job proved:

- configured production SSH access reaches the host;
- production images build and start;
- PostgreSQL becomes healthy;
- release migrations complete under the advisory lock;
- API and workers reach healthy state;
- container-internal `/health/live` and `/health/ready` return HTTP 200;
- deployment verification passes for API health, Telegram webhook configuration/authentication/backlog and configured payment routes.

Therefore **“deploy the Oracle backend” is no longer the current dogfood blocker**. Normal Bot_globa releases can already deploy automatically from `main`; a local VS Code/SSH session is not required for that normal release path.

What is still unproven is the public acquisition path and paid-release readiness:

1. `Bot_globa#58` — finish and verify the public `https://predict.mypresence.ru` route;
2. `Bot_globa#73` — fix the shared-host proxy-network alias/preflight and add public HTTPS smoke instead of relying only on container-internal health;
3. `Bot_globa#74` — create an isolated reproducible staging environment;
4. `Bot_globa#41` — execute the five real provider/model staging gates for the exact candidate release;
5. keep Oracle acquisition rollout at zero until routing and release-readiness are intentionally cleared.

A billing-disabled acquisition run cannot finish Partizan #10 because the milestone requires a real `PAID` conversion and calculable CAC. The intended sequence is:

```text
public route / deployment preflight
  -> isolated staging
  -> five live release gates + ready_for_limited_production
  -> limited Oracle rollout
  -> first real Partizan experiment
  -> VISIT / SIGNUP / ACTIVATED / PAID
  -> real CAC
  -> Growth Manager decision
  -> learning / next portfolio
```

The remaining Partizan dogfood proof is therefore real-world:

1. confirm a healthy public Oracle destination and release readiness;
2. run at least one real Partizan experiment to `RUNNING`;
3. receive real `VISIT / SIGNUP / ACTIVATED / PAID` events;
4. calculate real CAC;
5. obtain a data-backed Growth Manager decision;
6. persist the result into learning / next portfolio.

## Next order of work

1. finish the safe Founder Outreach visibility/review surface for #109;
2. review the bounded autonomous-send draft separately from the already merged preparation path;
3. clear Bot_globa public-route preflight (#58/#73);
4. build isolated staging and complete the five live release gates (#74/#41);
5. begin the limited Oracle rollout and run the first real Partizan acquisition experiment;
6. use dogfood evidence, not architecture speculation, to choose the next execution integrations.

## Product principle

**Execution over recommendations — but only inside explicit user-authorized boundaries.**

New integrations should improve the number of measurable customer-acquisition cycles Partizan can complete, not merely add more recommendation surfaces.
