# Partizan Bot — Current Implementation Status

This document is the current source of truth for implementation progress. `PRODUCT_PLAN.md` remains the long-form product vision and historical roadmap.

## Repository boundary

When Partizan work depends on another product or repository, that dependency is documented as an external blocker only. Partizan work must not modify another repository unless the product owner gives explicit permission for that specific external project/action.

Dogfood integrations therefore follow this rule:

1. Partizan exposes a documented integration contract;
2. the external product owner connects the product or explicitly authorizes repository work;
3. until that authorization exists, Partizan does not patch, deploy, migrate or otherwise mutate the external project.

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

## Milestone 8 — real-product dogfood (#10) — externally blocked

Milestone #10 remains open because its Definition of Done requires real users, a real `PAID` conversion, calculable CAC and a data-backed Growth Manager decision.

The previous Oracle-specific runbook remains historical dogfood tooling, but no external repository work is a Partizan task by default. If a chosen dogfood product is not ready, Partizan records that as an external dependency and continues improving its own runtime/integration surface.

A real dogfood run should only start after:

1. Partizan itself has a stable production runtime and public tracking origin;
2. the external product is connected through the documented conversion-event contract;
3. any required changes in that external product have been explicitly authorized by its owner;
4. at least one Partizan experiment can be launched within the configured provider/account boundaries.

## Current active milestone — productionize Partizan

The next engineering milestone is to make Partizan itself deployable and verifiable as a stable service rather than relying on `localhost` / production-like Docker execution.

Target production runtime:

```text
GitHub main
  -> CI
  -> explicit production deployment boundary
  -> sync release to Partizan host
  -> build production images
  -> migrate PostgreSQL
  -> start API + paid-control worker + autonomous-growth worker
  -> internal liveness/readiness smoke
  -> optional public HTTPS smoke
```

The production milestone must preserve these boundaries:

- deployment secrets remain GitHub/deployment secrets only;
- production uses database-backed runtime storage;
- PostgreSQL is not published publicly by the production compose file;
- migrations complete before API/workers start;
- `/health/live` checks process liveness without external dependencies;
- `/health/ready` proves PostgreSQL is reachable before traffic is considered ready;
- missing deployment secrets cause deployment to skip/fail safely rather than inventing host configuration;
- public HTTPS verification is enabled only when an explicit public Partizan URL is configured;
- production deployment does not mutate any external product/repository.

## Next order of work

1. production runtime + deployment workflow + liveness/readiness/smoke;
2. universal Product Integration Kit with generated Event Key guidance and integration verification;
3. generic product growth runner (remove Oracle as the architectural special case);
4. Partizan-local end-to-end sandbox for VISIT -> SIGNUP -> ACTIVATED -> PAID -> CAC -> Growth Manager -> learning;
5. real external dogfood only after explicit permission for any required external-project work;
6. use real dogfood evidence to choose later execution/provider integrations.

## Product principle

**Execution over recommendations — but only inside explicit user-authorized boundaries.**

New integrations should improve the number of measurable customer-acquisition cycles Partizan can complete, not merely add more recommendation surfaces.
