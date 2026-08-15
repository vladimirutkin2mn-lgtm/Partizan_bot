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

## Milestone 13 — Productionize Partizan (#121) — repository work complete, real infrastructure pending

All repository/code-side production work is implemented in `main`.

Runtime/deployment foundation:

- `docker-compose.prod.yml` with PostgreSQL private to the Docker network and API direct binding on host loopback only;
- one-shot migrations before API/workers;
- API, paid-control worker and autonomous-growth worker;
- `/health/live` plus PostgreSQL-backed `/health/ready`;
- fail-closed GitHub Actions production deployment;
- remote deployment and smoke scripts;
- production image/Compose/shell validation in CI.

Production hardening added in the final code-side slices:

- PR #131 / #130 — deny-by-default operator authentication for production `POST/PUT/PATCH/DELETE`; only the two product-scoped Event Key conversion data-plane POST routes are explicit exceptions;
- PR #133 / #132 — host-local `.env.prod` bootstrap with generated PostgreSQL/operator secrets plus fail-closed preflight before build, database start, migration or service mutation;
- PR #135 / #134 — database-backed heartbeats for both recurring workers, with restart reset, interval-relative staleness and deploy verification requiring a successful sweep from each current process;
- PR #137 / #136 — optional repository-managed Caddy HTTPS edge, loaded only for an explicit public Partizan URL, with automatic TLS state stored in host volumes and real Caddy parser validation in CI.

Canonical public-origin safety is also fail-closed: GitHub `PARTIZAN_PUBLIC_URL`, host `PARTIZAN_PUBLIC_BASE_URL` and `PARTIZAN_PUBLIC_HOST` must agree before a public deployment proceeds.

The latest `main` CI after PR #137 passes 483/483 pytest, development/production Compose validation, the real Caddy configuration parser and production image build.

The latest production workflow confirms the unconfigured-host boundary: without Partizan-specific deployment secrets, SSH agent setup, host-key pinning, host verification and deploy/migrate/smoke are all skipped. No host is guessed and no other project's deployment credentials are reused.

There is no remaining honest repository-only implementation step for #121. Completion now requires explicit real infrastructure:

1. provision/select a **dedicated Partizan production host**;
2. configure Partizan-specific GitHub deployment secrets (`DEPLOY_HOST`, `DEPLOY_SSH_KEY`, `DEPLOY_SSH_KNOWN_HOSTS`, `DEPLOY_PATH`);
3. run the host-local bootstrap and intentionally configure live providers/secrets in `.env.prod`;
4. choose a Partizan DNS hostname, point it to the host and allow inbound 80/443;
5. configure matching `PARTIZAN_PUBLIC_URL`, `PARTIZAN_PUBLIC_BASE_URL` and `PARTIZAN_PUBLIC_HOST`;
6. execute the first real deploy from `main`;
7. prove public HTTPS `/health/live` and `/health/ready`;
8. prove both recurring worker heartbeats remain healthy across one actual deployment/restart cycle.

Until those infrastructure inputs are explicitly supplied, #121 remains open rather than inventing another code milestone.

## Milestone 8 — real-product dogfood (#10) — remains open

Real dogfood still requires real users, at least one real `PAID` conversion, calculable CAC and a data-backed Growth Manager decision.

Start it only after:

1. #121 is completed and Partizan has its own public production origin;
2. the chosen product is connected through the Integration Kit;
3. any required work in that external product is explicitly authorized by its owner;
4. provider/account execution prerequisites for the chosen experiment are intentionally configured.

## Next order of work

1. complete the real infrastructure proof for #121: dedicated Partizan host, deployment secrets, DNS/public URL and first production deploy;
2. verify current-process worker heartbeats after a real restart and public HTTPS health;
3. connect a chosen real product through the Integration Kit;
4. ask for explicit permission before any required external-project modification;
5. run real dogfood #10 to a real PAID conversion, CAC and Growth Manager decision;
6. use real dogfood evidence—not architecture speculation—to choose subsequent provider/channel integrations.

`partizan-sandbox-run` can still be used as a synthetic release proof, but its results never count as real dogfood or acquisition performance.

## Product principle

**Execution over recommendations — but only inside explicit user-authorized boundaries.**

New work should increase the number of measurable customer-acquisition cycles Partizan can complete, not merely add more recommendation surfaces.
