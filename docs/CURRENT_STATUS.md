# Partizan Bot — Current Implementation Status

This document is the current source of truth for implementation progress. `PRODUCT_PLAN.md` remains the long-form product vision and historical roadmap.

## Current customer / production truth — 1 September 2026

- Partizan production is live. Milestone #121 is closed; releases deploy through the production workflow and public HTTPS/shared-host smoke verifies the exact release SHA.
- Customer onboarding is universal Product Understanding: website, app, bot, repository, other public product link or a short founder description.
- Research starts before spend. The customer can see Product Understanding and a real evidence-backed opportunity before acquisition funding is required.
- Growth Balance funding and paid-execution readiness are separate capabilities.
- **Issue #160 remains open.** Autonomous paid acquisition must stay fail-closed while the production provider-spend rail is unavailable or the project is not settlement-ready. `settlement_ready=false` is a hard blocker even when budget exists and Meta is connected.
- Do not describe a funded project as autonomous-paid-ready until #160 exit criteria are satisfied and real end-to-end dogfood proves provider spend and fee reconciliation.

## Repository boundary

When Partizan work depends on another product or repository, that dependency is an external blocker only. Partizan must not modify, deploy or migrate another project unless the product owner gives explicit permission for that specific external project/action.

The intended integration model is self-service:

1. Partizan exposes tracking, conversion and execution contracts;
2. the external product owner connects the product or explicitly authorizes external work;
3. without that authorization Partizan stays inside `Partizan_bot`.

## Current production architecture

```text
Product source / founder description
  -> Product Understanding / ProductProfile + targeted clarifications
  -> ranked ICPs
  -> Audience Intelligence / concrete Distribution Opportunities
  -> platform-aware Distribution Plays
  -> DistributionAction + Experiment
  -> permissioned / bounded execution adapters
  -> VISIT / SIGNUP / ACTIVATED / PAID attribution
  -> spend / CAC / ROAS
  -> SCALE / CONTINUE / MODIFY / STOP
  -> learning memory
  -> next portfolio / next bounded action
```

Original Milestones 0–7 are completed. Milestone 12 autonomous creator/partner outreach (#109) is also completed.

Production-oriented execution includes:

- permissioned owned Telegram execution;
- Meta and TikTok paid staging, exact-budget activation authorization, activation, sync, hard stop and reconciliation;
- permissioned TikTok owned publishing;
- first-click referral tracking and server-to-server conversion ingestion;
- Results & Learning workspace and Distribution Growth Manager decisions;
- evidence-backed creator/partner outreach with contact provenance and suppression;
- owned SMTP sending with restart-safe at-most-one submission attempt;
- explicit revocable outreach-send delegation and strict daily/domain/cooldown limits;
- fail-closed reconciliation after ambiguous provider outcomes;
- outreach attribution feeding Growth Manager and learning before another autonomous outreach action.

## Repository-wide pre-launch code audit (#139) — completed in code

The repository was reviewed end to end across API routes, browser workspace, persistence, workers, execution/provider boundaries, attribution/analytics, Growth Manager, migrations, production Compose/Caddy/deploy scripts and CI contracts.

Audit fixes are documented in `docs/CODE_AUDIT.md`. The launch-critical findings fixed in PRs #140 and #142 include:

- production UI/operator-auth mismatch that would have made `/app` render but core `/v1` actions fail with `401`;
- internal production `/v1` reads being less protected than writes;
- non-atomic event/spend idempotency under concurrent requests;
- a GET paid-campaign-spec route that could mutate state;
- readiness tied unnecessarily to async event-loop lifecycle;
- autonomous growth serialized only by a process-local lock even though API and worker run as separate processes.

Production control-plane access is deny-by-default for internal `/v1` reads and writes. Explicit public exceptions include the Product Event Key conversion data plane, intentionally public opaque creative blobs, customer-bound onboarding/project routes that enforce their own opaque customer token, the Meta OAuth callback, and Stripe-signed billing webhooks. Health, customer account/workspace routes and tracking routes live outside the internal `/v1` operator boundary.

The final cleanup retires the obsolete pre-distribution runtime (`ChannelHunter -> GrowthPlay -> ExecutionPackage -> legacy analytics/GrowthManager`), old mock/job scaffolding and product-specific dogfood compatibility code. The current browser workspace, generic growth runner and workers use the durable distribution domain only. A regression test prevents the retired runtime/routes from being reintroduced accidentally.

Historical Alembic revisions and SQLAlchemy model metadata are intentionally retained. They are schema/migration history and must not be rewritten merely because the early HTTP runtime was retired.

Repository CI can prove checked-in code, migration, JavaScript, Compose/Caddy, sandbox and image-build contracts. It cannot prove live provider credentials, provider-account permissions, DNS, a dedicated production host or real acquisition performance; those remain operational/dogfood milestones below.

## Milestone 14 — Universal Product Integration Kit (#122) — completed

Completed through merged PRs #123–#126.

A product can connect to Partizan without Partizan developers patching its repository:

- `GET /v1/products/{product_id}/integration-status` shows Event Key/public tracking/experiment readiness and real observed VISIT/SIGNUP/ACTIVATED/PAID signals without exposing the key;
- `POST /v1/products/{product_id}/distribution-events/verify` validates the real conversion contract and attribution while guaranteeing `persisted=false`;
- `GET /v1/products/{product_id}/integration-guide` generates product-specific cURL, Python and Node.js examples with secret placeholders only;
- `/app` contains read-only Integration Readiness and copyable integration-code workspaces;
- the guide documents stable event IDs, backend-only Event Keys and transactional outbox/retry semantics;
- `partizan-growth-run` is the primary product-agnostic CLI for Product -> distribution -> experiment traversal;
- free-text runs require explicit `--answer field=value` for material clarifications rather than guessing;
- dry-run is the default and paid execution still stops at `STAGED`; the generic runner cannot authorize/activate spend.

There is no product-specific dogfood runner in the active architecture; product-specific integration belongs outside Partizan unless separately authorized.

## Milestone 15 — Isolated end-to-end growth sandbox (#127) — completed

Merged PR #128 adds `partizan-sandbox-run`.

The sandbox proves the real internal Partizan loop from a clean isolated child process:

```text
ProductProfile
  -> ICP
  -> distribution/play
  -> action + RUNNING experiment
  -> VISIT / SIGNUP / ACTIVATED / PAID
  -> spend
  -> CAC / ROAS
  -> Growth Manager
  -> learning memory
  -> next portfolio using observed economics
```

Isolation is deliberate:

- refuses to run when the parent is `APP_ENV=production`;
- child starts from a temporary directory with `RUNTIME_STORAGE=memory`;
- LLM/search/execution are mock and creative providers unavailable;
- no database, OpenAI, Gemini, SMTP, operator, Meta or TikTok secret is passed to the child;
- no external execution-provider mutation is called;
- child state disappears when the subprocess exits;
- report is explicitly labeled `SANDBOX — SYNTHETIC / NOT PRODUCTION DATA`.

The deterministic proof uses three synthetic complete funnels, $30 total spend and $90 revenue, yielding CAC=$10 and ROAS=3.0, then verifies the real Growth Manager `SCALE` decision, one learning entry and a next portfolio that incorporates the observed economics.

This is an internal correctness proof only. It does **not** count as real acquisition performance and does not satisfy dogfood #10.

## Milestone 13 — Productionize Partizan (#121) — completed

Issue #121 closed on 27 August 2026. Production infrastructure is configured and the application deploys from `main` through the fail-closed GitHub Actions production workflow.

Runtime/deployment foundation:

- `docker-compose.prod.yml` with PostgreSQL private to the Docker network and API direct binding on host loopback only;
- one-shot migrations before API/workers;
- API, paid-control worker and autonomous-growth worker;
- `/health/live` plus PostgreSQL-backed `/health/ready`;
- fail-closed GitHub Actions production deployment;
- remote deployment and smoke scripts;
- production image/Compose/shell validation in CI.

Production hardening includes:

- deny-by-default operator authentication for production internal `/v1` reads and writes, with only explicit public data-plane exceptions;
- host-local `.env.prod` bootstrap with generated PostgreSQL/operator secrets plus fail-closed preflight before build, database start, migration or service mutation;
- database-backed heartbeats for both recurring workers, with restart reset, interval-relative staleness and deploy verification requiring a successful sweep from each current process;
- PostgreSQL advisory-lock serialization of autonomous sweeps across API and worker processes;
- optional repository-managed Caddy HTTPS edge, loaded only for an explicit public Partizan URL, with automatic TLS state stored in host volumes and real Caddy parser validation in CI.

Canonical public-origin safety is fail-closed: GitHub `PARTIZAN_PUBLIC_URL`, host `PARTIZAN_PUBLIC_BASE_URL` and `PARTIZAN_PUBLIC_HOST` must agree before a public deployment proceeds.

Without Partizan-specific deployment secrets, SSH agent setup, host-key pinning, host verification and deploy/migrate/smoke are skipped. No host is guessed and no other project's deployment credentials are reused.

The infrastructure steps above have been completed. Production now runs in shared-host mode behind the existing HTTPS proxy. Deployment verifies the exact `PARTIZAN_RELEASE_SHA`, public health/readiness and worker health before declaring a release successful. Browser-surface release integrity is a separate delivery invariant and is being hardened so a current `/version` cannot mask stale UI.

## Milestone 8 — real-product dogfood (#10) — remains open

Real dogfood still requires real users, at least one real `PAID` conversion, calculable CAC and a data-backed Growth Manager decision.

Start it with the live production origin, but keep execution honest:

1. choose real external products and run the universal Product Understanding + research flow;
2. obtain explicit authorization before any required external-product modification;
3. measure source recognition, clarification rate, Product Understanding corrections, evidence quality and useful-opportunity rate;
4. for paid dogfood, complete #160 first and verify the project-specific provider-spend rail is settlement-ready before activating autonomous paid acquisition.

## Next order of work

1. close #160 by production-enabling and monitoring the provider-spend rail, project isolation/limits, provider billing binding and authoritative spend/refund reconciliation;
2. run external Product Understanding/research dogfood across real products now, without pretending paid execution is available;
3. once #160 is complete, run a funded end-to-end paid dogfood cycle to real provider spend, PAID conversion, CAC, fee reconciliation and Growth Manager decision;
4. use real dogfood evidence—not architecture speculation—to choose subsequent provider/channel integrations.

`partizan-sandbox-run` can still be used as a synthetic release proof, but its results never count as real dogfood or acquisition performance.

## Product principle

**Execution over recommendations — but only inside explicit user-authorized boundaries.**

New work should increase the number of measurable customer-acquisition cycles Partizan can complete, not merely add more recommendation surfaces.
