# Customer Autopilot Bridge

## Goal

Turn the paid customer funnel into a real execution path without creating a second growth engine.

```text
Free pre-scan
  → $49 Acquisition Plan
  → Product / ICP / Audience Intelligence
  → $149/month Autopilot subscription
  → delegated marketing budget + max CAC
  → self-service Meta connection
  → existing Growth Mandate
  → existing autonomous-growth worker
  → Meta staging / exact-budget activation / paid control
  → conversion analytics / CAC / ROAS
  → Growth Manager / learning / next experiment
```

The customer surface is `/start`. The operator workspace remains `/app`.

## Billing

The Acquisition Plan remains a one-time Stripe Checkout purchase.

Autopilot uses a separate recurring Stripe Price and `mode=subscription`. The subscription is bound to the exact customer project through Stripe metadata. A browser return is never sufficient proof: Partizan retrieves the Checkout Session and Subscription server-side, while signed Stripe webhooks provide the out-of-band source of truth.

Accepted subscription states:

- `active` / `trialing` → customer Autopilot entitlement is `ACTIVE`;
- `past_due` / `unpaid` / `paused` → entitlement is `PAST_DUE`;
- `canceled` → entitlement is `CANCELLED`.

If billing leaves the active state, an ACTIVE Growth Mandate is automatically PAUSED. Billing recovery does not silently resume spend; the customer must resume Autopilot explicitly.

Production readiness verifies both commercial Stripe prices:

- Acquisition Plan: active, one-time, USD, `$49`;
- Autopilot: active, recurring monthly, USD, `$149`.

## Marketing budget model

The marketing budget is a delegation cap, not a Partizan wallet.

The customer chooses:

- total marketing budget;
- target maximum CAC;
- optional per-experiment and per-day autonomous limits;
- explicit confirmation that Partizan may activate paid experiments inside those limits.

The customer bridge initially delegates only:

- platform: `INSTAGRAM` / Meta Ads;
- action: `PAID_CAMPAIGN`;
- autonomous prepare: enabled;
- autonomous approval: enabled;
- autonomous paid activation: enabled;
- maximum concurrent running experiments: 2.

The existing Growth Mandate evaluator, paid-provider staging, one-time exact-budget activation, paid-control worker and Growth Manager remain authoritative. The customer bridge cannot bypass those controls.

If the customer has configured a budget but Meta is not connected, the mandate is immediately PAUSED with a setup blocker. A successful Meta connection may activate that setup-paused mandate. A customer or billing pause is never auto-resumed by Meta connection.

## Self-service Meta connection

The customer starts OAuth from `/start`.

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

Existing Meta execution adapters already consume environment secret references. For compatibility, the connection service decrypts a customer token when the connection is loaded and hydrates that generated reference into process memory. No plaintext token is written back to RuntimeStateStore or API responses.

Graph resource requests send the access token in the `Authorization: Bearer ...` header, not in query parameters.

## Customer dashboard

The `/start` Autopilot panel reads the same observed distribution analytics as the operator workspace and shows:

- delegated budget;
- observed spend;
- remaining delegated budget;
- paid customers;
- revenue;
- CAC;
- ROAS;
- current Meta connection;
- running / waiting experiments;
- recent autonomous decisions;
- pause / resume controls.

The displayed managed-spend fee is currently an estimate based on observed spend:

```text
observed spend × PARTIZAN_MANAGED_SPEND_FEE_PCT
```

This bridge does **not** automatically invoice or collect the 10% managed-spend fee. Automated usage invoicing should be implemented as a separate billing milestone so it cannot be confused with the advertising budget or silently charged from customer ad spend.

## Required public-production configuration

In addition to the existing production secrets:

```text
STRIPE_SECRET_KEY
STRIPE_WEBHOOK_SECRET
STRIPE_LAUNCH_PRICE_ID
STRIPE_AUTOPILOT_PRICE_ID
PROVIDER_SECRET_ENCRYPTION_KEY
META_OAUTH_APP_ID
META_OAUTH_APP_SECRET
META_OAUTH_API_VERSION
PARTIZAN_PUBLIC_BASE_URL
PARTIZAN_PUBLIC_HOST
```

`bootstrap_prod_host.sh` generates `PROVIDER_SECRET_ENCRYPTION_KEY` locally and never prints it. Meta app values and Stripe Price IDs must be supplied explicitly. Public preflight fails closed while any of these customer-facing dependencies are missing.

## Non-goals of this slice

- automated collection of the 10% managed-spend fee;
- self-service TikTok Ads OAuth;
- arbitrary customer control over every internal action/channel;
- bypassing explicit platform permissions or provider reconciliation;
- storing Meta access tokens in browser storage, URLs or plaintext database fields.

TikTok and additional providers should be added only after the first real Meta Autopilot dogfood proves the customer bridge and economics end to end.
