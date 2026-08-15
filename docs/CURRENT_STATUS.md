# Partizan Bot — Current Implementation Status

This document is the current source of truth for implementation progress. `PRODUCT_PLAN.md` remains the long-form product vision and historical roadmap.

## Repository boundary

When Partizan work depends on another product or repository, that dependency is an external blocker only. Partizan must not modify, deploy or migrate another project unless the product owner gives explicit permission for that specific external project/action.

The intended integration model is self-service:

1. Partizan exposes tracking, conversion and execution contracts;
2. the external product owner connects the product or explicitly authorizes external work;
3. without that authorization Partizan stays inside `Partizan_bot`.

## Core growth loop in `main`

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
  -> learning memory
  -> next portfolio / next bounded action
```

Original Milestones 0–7 are completed. Milestone 12 autonomous creator/partner outreach (#109) is also completed.

Production-oriented execution already includes:

- permissioned owned Telegram execution;
- Meta and TikTok paid staging, exact-budget activation authorization, activation, sync, hard stop and reconciliation;
- permissioned TikTok owned publishing;
- first-click referral tracking and server-to-server conversion ingestion;
- Results & Learning workspace and Growth Manager decisions;
- evidence-backed creator/partner outreach with contact provenance and suppression;
- owned SMTP sending with restart-safe at-most-one submission attempt;
- explicit revocable outreach-send delegation and strict daily/domain/cooldown limits;
- fail-closed reconciliation after ambiguous provider outcomes;
- outreach attribution feeding Growth Manager and learning before another autonomous outreach action.

## Milestone 14 — Universal Product Integration Kit (#122) — completed

Completed through merged PRs #123–#126.

A product can now connect to Partizan without Partizan developers patching its repository:

- `GET /v1/products/{product_id}/integration-status` shows Event Key/public tracking/experiment readiness and real observed VISIT/SIGNUP/ACTIVATED/PAID signals without exposing the key;
- `POST /v1/products/{product_id}/distribution-events/verify` validates the real conversion contract and attribution while guaranteeing `persisted=false`;
- `GET /v1/products/{product_id}/integration-guide` generates product-specific cURL, Python and Node.js examples with secret placeholders only;
- `/app` contains read-only Integration Readiness and copyable integration-code workspaces;
- the guide documents stable event IDs, backend-only Event Keys and transactional outbox/retry semantics;
- `partizan-growth-run` is now the primary product-agnostic CLI for Product -> distribution -> experiment traversal;
- free-text runs require explicit `--answer field=value` for material clarifications rather than guessing;
- dry-run is the default and paid execution still stops at `STAGED`; the generic runner cannot authorize/activate spend.

`partizan-dogfood-oracle` remains only as a compatibility preset for the historical first dogfood scenario. Oracle is no longer an architectural requirement.

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

## Milestone 13 — Productionize Partizan (#121) — current active blocker

The code-side production runtime is implemented:

- `docker-compose.prod.yml` with non-public PostgreSQL;
- one-shot migrations before API/workers;
- API, paid-control worker and autonomous-growth worker;
- `/health/live` and PostgreSQL-backed `/health/ready`;
- fail-closed GitHub Actions production deployment;
- remote deploy and smoke scripts;
- optional public HTTPS smoke;
- CI validation of production shell/Compose/image contracts.

What is still genuinely external configuration, and therefore not completed in code:

1. configure a **dedicated Partizan production host/environment** and Partizan-specific GitHub deployment secrets;
2. run the first successful Partizan production deploy from `main`;
3. configure explicit `PARTIZAN_PUBLIC_URL` / `PARTIZAN_PUBLIC_BASE_URL`;
4. prove public HTTPS `/health/live` and `/health/ready`;
5. prove API and workers stay healthy across a deployment/restart cycle.

The current deploy workflow safely skips deployment when Partizan-specific SSH configuration is absent. No host is guessed and no other project's deployment credentials are reused.

## Milestone 8 — real-product dogfood (#10) — remains open

Real dogfood still requires real users, at least one real `PAID` conversion, calculable CAC and a data-backed Growth Manager decision.

Start it only after:

1. #121 is completed and Partizan has its own public production origin;
2. the chosen product is connected through the Integration Kit;
3. any required work in that external product is explicitly authorized by its owner;
4. provider/account execution prerequisites for the chosen experiment are intentionally configured.

## Next order of work

1. complete the real infrastructure portion of #121: dedicated Partizan host, secrets, public URL and production smoke;
2. run `partizan-sandbox-run` as a release proof when useful, while keeping its data explicitly synthetic;
3. connect a chosen real product through the Integration Kit;
4. ask for explicit permission before any required external-project modification;
5. run real dogfood #10 to a real PAID conversion, CAC and Growth Manager decision;
6. use real dogfood evidence—not architecture speculation—to choose subsequent provider/channel integrations.

## Product principle

**Execution over recommendations — but only inside explicit user-authorized boundaries.**

New work should increase the number of measurable customer-acquisition cycles Partizan can complete, not merely add more recommendation surfaces.