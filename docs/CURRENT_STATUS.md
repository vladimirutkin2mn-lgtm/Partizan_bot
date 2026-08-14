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
  -> permissioned / bounded execution
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
- evidence-backed creator/partner OutreachTarget with contact provenance and suppression;
- truthful personalized OutreachBrief + offer + exact referral attribution;
- owned SMTP sender readiness and restart-safe one-message send ledger;
- bounded Outreach Policy with autonomous target/draft preparation;
- explicit, revocable autonomous outreach-send delegation pinned to exact policy, Growth Mandate and sender versions;
- hard autonomous outreach limits: at most 5 initial messages/day, at most 1/contact-domain/day, target/domain cooldowns, zero autonomous follow-up;
- ambiguous SMTP outcomes become `RECONCILIATION_REQUIRED` and block further autonomous outreach rather than being retried blindly;
- Founder Outreach workspace showing evidence, ICP overlap, offer/message, sender/policy, delivery/reconciliation and attributed conversions;
- separate Autonomous Outreach workspace for explicit delegate/pause/resume/revoke controls; the browser cannot trigger SMTP sending;
- outreach conversion changes feed the existing Growth Manager and learning memory before another autonomous outreach action is attempted.

## Milestone 12 — Autonomous Creator & Partner Outreach (#109) — completed

Issue #109 is closed as completed. The full implemented loop is now:

```text
ChannelOpportunity / ActionTarget
  -> evidence-backed OutreachTarget
  -> personalized OutreachBrief + Offer
  -> exact message draft
  -> policy / suppression / sender checks
  -> explicit bounded execution delegation
  -> restart-safe owned SMTP send
  -> referral / conversion attribution
  -> Growth Manager decision
  -> learning memory
  -> next portfolio / next bounded action
```

Safety invariants remain intentionally strict:

- no guessed or synthesized email addresses;
- no private/personal contact scraping;
- no purchased/breached contact lists;
- no mass unsolicited-email engine;
- no autonomous follow-up in the current MVP;
- no sender/domain rotation to evade reputation controls;
- no blind resend after an ambiguous provider outcome;
- exact target / offer / message / experiment attribution is preserved;
- SMTP credentials remain deployment-secret-only;
- changes to Outreach Policy, Growth Mandate or sender identity invalidate the autonomous-send delegation until it is explicitly reissued.

The milestone was completed through the merged Founder Outreach workspace, bounded autonomous-send execution and final outreach-learning/delegation UI slices. Validation-only issue #115 is also closed.

## Current active milestone — real-product dogfood (#10)

**Milestone 8 — Dogfood on a real product** is now the next active proof. Code readiness is not enough to close it.

Chosen product: `Bot_globa / Oracle`.

Business assumptions for the first acquisition loop:

- subscription: `$6.90/month`;
- initial acquisition budget: `$1,000`;
- target max CAC: `$12` per paid subscriber;
- initial audience: English-speaking adults roughly 20–40 interested in astrology, relationships and self-reflection;
- initial distribution scope: Telegram / Instagram / Reddit / TikTok plus bounded creator/partner outreach where policy permits.

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

Therefore **deploying the Oracle backend is not the current dogfood blocker**. Normal Bot_globa releases can deploy automatically from `main`; a local VS Code/SSH session is not required for the normal release path.

What is still unproven is the public acquisition path and paid-release readiness:

1. `Bot_globa#58` — finish and verify the public `https://predict.mypresence.ru` route;
2. `Bot_globa#73` — fix the shared-host proxy-network alias/preflight and add public HTTPS smoke instead of relying only on container-internal health;
3. `Bot_globa#74` — create an isolated reproducible staging environment;
4. `Bot_globa#41` — execute the five real provider/model staging gates for the exact candidate release;
5. keep Oracle acquisition rollout at zero until routing and release-readiness are intentionally cleared.

A billing-disabled acquisition run cannot finish Partizan #10 because the milestone requires a real `PAID` conversion and calculable CAC.

The intended sequence is:

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

1. clear Bot_globa public-route/preflight blockers (#58/#73);
2. build isolated staging and complete the five live release gates (#74/#41);
3. begin the limited Oracle rollout;
4. run the first real Partizan acquisition experiment and collect a real `PAID` conversion;
5. use dogfood evidence, not architecture speculation, to choose the next execution integrations.

## Product principle

**Execution over recommendations — but only inside explicit user-authorized boundaries.**

New integrations should improve the number of measurable customer-acquisition cycles Partizan can complete, not merely add more recommendation surfaces.
