# Customer Autopilot Bridge

## Goal

Turn the customer funnel into a real execution path without creating a second growth engine or a recurring execution subscription.

```text
Product Understanding + one evidence-backed opportunity before funding
  ├─→ $49 Acquisition Plan only
  │    → deeper Product / ICP / Audience Intelligence
  │    → research-only strategy for the customer
  │
  └─→ Concrete paid move needs budget
       → add Growth Balance for that move
       → deeper Product / ICP / Audience Intelligence included
       → self-service Meta connection
       → max CAC + explicit autonomous-spend consent
       → wait for production spend rail readiness
       → existing Growth Mandate
       → existing autonomous-growth worker
       → Meta staging / exact-budget activation / paid control
       → conversion analytics / CAC / ROAS
       → Growth Manager / learning / next experiment
```

The customer surface is `/start`. The operator workspace remains `/app`.

## Monetization

The free product analysis produces Product Understanding and attempts to find a real evidence-backed acquisition opportunity before asking for funding.

The paid paths are:

1. **Acquisition Plan — $49 one time.** The customer buys the deeper audience/channel research without autonomous execution.
2. **Growth Balance for a concrete paid move — 10% of actual acquisition spend.** There is no monthly subscription. Funding a paid move includes the deeper Product/ICP/Audience research required by the workspace, so this customer does not also need the $49 research-only upgrade.

**Current production truth:** Growth Balance funding and autonomous paid execution are separate capabilities. Issue #160 remains open. Until the production provider-spend rail is configured, bound and reconciled, `settlement_ready=false` must keep autonomous paid activation blocked. The UI and docs must not describe a funded project as paid-execution-ready merely because Meta is connected or budget exists.

Growth Balance is an all-in prepaid acquisition budget. If a customer funds `$1,000` and the management fee is 10%, the maximum acquisition capacity is `$909.09`; the remaining `$90.91` is the maximum fee only if all that acquisition capacity is actually spent. If Partizan spends only `$600`, the fee is `$60` and `$340` remains in Growth Balance.

The one-time Acquisition Plan still uses a fixed Stripe Price. Growth Balance uses dynamic one-time Stripe Checkout amounts and has no recurring Stripe Price.

## Growth Balance execution model

Growth Balance is the customer's hard money boundary. Before accepting a top-up, Partizan checks that the Partizan-funded provider payment rail has enough prefunded liquidity to honor the incremental acquisition capacity.

A successful Growth Balance payment:

- credits the exact paid amount to the project ledger;
- grants the acquisition-research entitlement if the customer did not buy the $49 plan;
- provisions or updates the Partizan-owned provider payment rail;
- lets Partizan run Product / ICP / Audience research under the hood;
- does **not** by itself authorize spend.

Autonomous paid execution still requires:

- completed research;
- a real destination URL;
- funded Growth Balance with remaining acquisition capacity;
- self-service Meta connection;
- a positive maximum CAC;
- explicit customer confirmation of autonomous paid experiments;
- a ready/bound Partizan-funded settlement rail;
- an ACTIVE Growth Mandate.

The existing Growth Mandate evaluator, paid-provider staging, one-time exact-budget activation, paid-control worker and Growth Manager remain authoritative. The customer bridge cannot bypass those controls.

## Self-service Meta connection

After Growth Balance funding unlocks and Partizan completes the internal acquisition research, the customer starts OAuth from `/start`.

Partizan requests the ad-management/read permissions needed by the current Meta Marketing API integration and uses an explicit deployment-configured Graph API version. No moving API-version default is guessed in code.

The OAuth state is:

- random;
- valid for 15 minutes;
- stored only by SHA-256 digest;
- one-time and marked consumed before provider exchange.

After OAuth, Partizan loads manageable ad accounts and available promotion Pages. The customer selects the exact ad account, Page and target country before a `PaidProviderConnection` is created.

### Token boundary

The Meta user access token is never returned to browser JavaScript or persisted in plaintext.

`PROVIDER_SECRET_ENCRYPTION_KEY` encrypts customer provider tokens with Fernet before writing them to the existing RuntimeStateStore. The persisted Meta connection keeps only a generated secret reference such as:

```text
CUSTOMER_META_ACCESS_TOKEN_<opaque-id>
```

Existing Meta execution adapters consume environment secret references. For compatibility, the connection service decrypts a customer token when the connection is loaded and hydrates that generated reference into process memory. No plaintext token is written back to RuntimeStateStore or API responses.

Graph resource requests send the access token in the `Authorization: Bearer ...` header, not in query parameters.

## Customer dashboard

The `/start` execution panel reads the same observed distribution analytics as the operator workspace and shows:

- Growth Balance funded / available;
- actual acquisition spend;
- Partizan fee earned from actual spend;
- paid customers;
- revenue;
- CAC;
- current Meta connection;
- running / waiting experiments;
- recent autonomous decisions;
- pause / resume controls.

For Stripe Issuing-backed projects, net Issuing captures/refunds are the financial source of truth for acquisition spend. The fee is:

```text
actual acquisition spend × PARTIZAN_MANAGED_SPEND_FEE_PCT
```

## Required public-production configuration

In addition to the existing production secrets:

```text
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
STRIPE_LAUNCH_PRICE_ID
PROVIDER_SECRET_ENCRYPTION_KEY
META_OAUTH_APP_ID
META_OAUTH_APP_SECRET
META_OAUTH_API_VERSION
PARTIZAN_PUBLIC_BASE_URL
PARTIZAN_PUBLIC_HOST
```

There is intentionally no `STRIPE_AUTOPILOT_PRICE_ID`: autonomous execution has no recurring subscription Price.

The Growth Balance settlement rail has its own conditional Stripe Issuing configuration documented in `GROWTH_BALANCE_ISSUING.md`. `bootstrap_prod_host.sh` keeps that rail unavailable until explicitly configured. Public preflight still fails closed on required customer-facing Stripe/Meta configuration and, when Issuing is enabled, on all required money-rail configuration.

## Non-goals of this slice

- self-service TikTok Ads OAuth;
- arbitrary customer control over every internal action/channel;
- bypassing explicit platform permissions or provider reconciliation;
- storing Meta access tokens in browser storage, URLs or plaintext database fields;
- treating the database ledger as a withdrawable customer wallet.

TikTok and additional providers should be added only after the first real Meta dogfood proves the customer bridge and economics end to end.
