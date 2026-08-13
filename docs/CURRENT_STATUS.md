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
2. final explicit validation that outreach acquisition results feed the existing analytics -> Growth Manager -> learning loop;
3. any autonomous execution extension must remain separately delegated, low-volume, zero-follow-up and fail closed on ambiguous provider outcomes.

PR #114 contains a draft implementation of the narrowly delegated bounded autonomous send slice. It is intentionally not part of `main` until that execution boundary is approved for merge.

Issue #115 tracks validation of the existing Results/Learning integration without expanding provider capabilities.

## Real-product dogfood — #10

**Milestone 8 — Dogfood on a real product** remains open by design. Code readiness is not enough to close it.

Chosen product: `Bot_globa / Oracle`.

Business assumptions for the first acquisition loop:

- subscription: `$6.90/month`;
- initial acquisition budget: `$1,000`;
- target max CAC: `$12` per paid subscriber;
- initial audience: English-speaking adults roughly 20–40 interested in astrology, relationships and self-reflection;
- initial distribution scope: Telegram / Instagram / Reddit / TikTok.

The remaining dogfood proof is real-world:

1. deploy Oracle to a healthy public destination;
2. run at least one real Partizan experiment to `RUNNING`;
3. receive real `VISIT / SIGNUP / ACTIVATED / PAID` events;
4. calculate real CAC;
5. obtain a data-backed Growth Manager decision;
6. persist the result into learning / next portfolio.

The Oracle deployment prerequisite is tracked in `vladimirutkin2mn-lgtm/Bot_globa#58`.

## Next order of work

1. finish the safe Founder Outreach visibility/review surface for #109;
2. close the outreach -> analytics -> Growth Manager -> learning validation gap;
3. review the bounded autonomous-send draft separately from the already merged preparation path;
4. deploy Oracle and run the real dogfood loop;
5. use dogfood evidence, not architecture speculation, to choose the next execution integrations.

## Product principle

**Execution over recommendations — but only inside explicit user-authorized boundaries.**

New integrations should improve the number of measurable customer-acquisition cycles Partizan can complete, not merely add more recommendation surfaces.
