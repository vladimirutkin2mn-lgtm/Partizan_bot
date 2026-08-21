# Growth Balance — funding now, controlled spend rail later

Growth Balance has two deliberately separate responsibilities:

1. **Funding rail** — the customer adds money with the existing Stripe Payments / Checkout integration.
2. **Spend rail** — Partizan pays advertising providers from that balance through a controlled Partizan-funded rail.

For the current MVP these two capabilities are intentionally decoupled. Customers may fund Growth Balance with Stripe Checkout while `GROWTH_BALANCE_SETTLEMENT_PROVIDER=unavailable`. The balance is credited, displayed, and grants the acquisition-research entitlement, but autonomous paid acquisition remains fail-closed because `settlement_ready=false`.

The required follow-up is tracked in GitHub issue **#160 — Connect Growth Balance spend rail before autonomous paid acquisition**. Do not close that issue merely because Checkout top-ups work.

The product contract remains:

- autonomous execution has no monthly subscription;
- Growth Balance is the customer's prepaid all-in growth budget;
- Partizan's variable fee is 10% of actual acquisition spend;
- funding Growth Balance includes the acquisition research required for execution;
- a funded balance is **not** permission or technical ability to spend it;
- paid activation requires a real, ready provider-spend rail in addition to the normal channel and guardrail gates.

## Temporary MVP mode: Stripe Checkout only

Keep:

```dotenv
GROWTH_BALANCE_SETTLEMENT_PROVIDER=unavailable
```

The existing Stripe Checkout settings are enough to accept a Growth Balance top-up:

```dotenv
STRIPE_SECRET_KEY=sk_...
STRIPE_WEBHOOK_SECRET=whsec_...
```

`STRIPE_LAUNCH_PRICE_ID` remains the optional one-time `$49` Acquisition Plan Price. Growth Balance itself uses a dynamic USD Stripe Checkout amount and has no recurring Autopilot Price.

In checkout-only mode Partizan:

1. validates the customer project and requested amount;
2. creates a short-lived Growth Balance reservation in the internal ledger;
3. opens ordinary Stripe Checkout;
4. verifies the paid Checkout Session by project, entitlement, generation, amount and currency;
5. credits the exact paid amount idempotently;
6. unlocks the included acquisition research;
7. keeps `settlement_ready=false` and therefore keeps autonomous paid activation paused.

No fake card, fake provider binding, or fake `settlement_ready=true` state is created. Customer funds must not be represented as already spendable at Meta, TikTok, Reddit, Telegram, or any other provider.

## Target production spend rail: Stripe Issuing

The intended controlled spend rail remains a pre-funded Stripe Issuing liquidity pool with one Partizan-owned virtual card per customer project.

Each project card should be:

- virtual;
- USD-only for the first production version;
- restricted to Stripe merchant category `advertising_services`;
- capped by an all-time spending limit equal to that project's Growth Balance acquisition capacity;
- created inactive;
- activated only after the exact provider billing account is bound and all Partizan safety gates pass.

Partizan must never store or return PAN/CVC/card-expiry values.

When the Issuing rail is eventually enabled, Stripe Issuing captures/refunds become the financial source of truth for acquisition spend. Provider analytics remain useful for campaign performance but do not decide how much money has left Growth Balance.

## 1. Enable and pre-fund Stripe Issuing

Before switching the production provider from `unavailable` to `stripe_issuing`:

1. Enable Stripe Issuing on the Partizan Stripe account and complete any Stripe requirements.
2. Create or choose a Partizan company Issuing Cardholder.
3. Pre-fund the Stripe Issuing balance with enough USD liquidity for accepted customer acquisition commitments.
4. Configure both Issuing webhook endpoints and their secrets.
5. Verify provider-side billing attachment for the owner dogfood project.
6. Only then change `GROWTH_BALANCE_SETTLEMENT_PROVIDER=stripe_issuing`.

Once `stripe_issuing` is enabled, Partizan resumes the strict Issuing liquidity check before opening each **new** Growth Balance Checkout. It checks current Issuing availability against outstanding acquisition commitments plus the new incremental capacity.

## 2. Configure production Issuing secrets

```dotenv
GROWTH_BALANCE_SETTLEMENT_PROVIDER=stripe_issuing
STRIPE_ISSUING_CARDHOLDER_ID=ich_...
STRIPE_ISSUING_CURRENCY=usd
STRIPE_ISSUING_AUTHORIZATION_WEBHOOK_SECRET=whsec_...
STRIPE_ISSUING_EVENTS_WEBHOOK_SECRET=whsec_...
STRIPE_ISSUING_WEBHOOK_API_VERSION=2025-03-31.basil
```

Do not put card numbers, CVCs or expiry values in Partizan environment variables, logs, the database, GitHub, tickets, or chat.

## 3. Configure the synchronous authorization webhook

Endpoint:

```text
https://<partizan-host>/v1/billing/stripe/issuing-authorizations
```

Paid authorization must fail closed unless all applicable gates pass, including:

- known project rail;
- exact provider billing binding;
- active card;
- allowed currency and advertising merchant category;
- amount inside remaining Growth Balance acquisition capacity;
- ACTIVE Growth Mandate.

The Stripe card-level MCC and all-time limit remain an independent boundary.

## 4. Configure the Issuing transaction webhook

Endpoint:

```text
https://<partizan-host>/v1/billing/stripe/issuing-events
```

Subscribe to the Issuing transaction create/update events required by the pinned Stripe API version. Persist only non-sensitive accounting data. Captures increase acquisition spend, refunds decrease it, and re-delivery must remain idempotent by Stripe Issuing transaction ID.

Unexpected captured currency or merchant category must pause the rail fail-closed.

## 5. Production preflight

Run:

```bash
PARTIZAN_REQUIRE_PUBLIC_URL=true bash tools/preflight_prod_host.sh .env.prod
```

In checkout-only mode, ordinary Stripe Checkout can fund Growth Balance without Issuing configuration. In `stripe_issuing` mode, preflight must require the Issuing Cardholder, webhook secrets, USD currency and explicit API version. Do not bypass preflight to test a partially configured spend rail.

## 6. Customer funding lifecycle

Growth Balance Checkout expires after 30 minutes. Partizan's internal reservation is held slightly longer to cover delivery/timing skew.

On a verified paid Checkout, Partizan credits the exact amount and grants the internal acquisition-research entitlement. A signed paid event may recover a session from the pre-Checkout reservation if persistence lost the session-id write.

The distinction is critical:

```text
funded Growth Balance != spend-ready Growth Balance
```

Research and other non-spend setup may proceed after funding. Autonomous paid activation may not.

## 7. Bind the future Partizan card to provider billing

The current MVP **does not** automate adding the Partizan Issuing card to Meta billing. When Issuing is enabled, use an approved secure Stripe card-detail surface and provider billing UI/process. Never copy PAN/CVC through Partizan APIs or persistence.

After provider-side attachment, record only the non-sensitive binding fact through the protected operator endpoint:

```text
POST /v1/customer-projects/{project_id}/growth-balance/rail/meta-binding
```

Do not confirm a binding until the exact customer provider account is verified.

## 8. Paid activation

A customer Resume / paid activation must require all product safety gates and, at minimum:

- positive remaining Growth Balance acquisition capacity;
- acquisition research ready;
- required provider/channel connection ready;
- customer max-CAC / autonomous-spend authorization saved;
- controlled provider-spend rail ready.

Therefore checkout-only mode intentionally lets the customer fund and prepare the project while keeping paid acquisition paused.

## 9. Accounting after the spend rail is live

```text
actual acquisition spend = authoritative net spend-rail captures/refunds
Partizan variable fee     = 10% × actual acquisition spend
Growth Balance used       = acquisition spend + variable fee
Growth Balance available  = funded amount - used amount
```

Do not charge the 10% fee on the deposit itself and do not substitute provider-reported campaign spend for authoritative financial settlement when the controlled spend rail is live.

## 10. First live dogfood with Issuing

Before the first Partizan-funded paid sweep verify:

- Stripe Issuing is enabled and pre-funded;
- project-level card/limit isolation exists;
- exact provider account is bound;
- Growth Mandate is ACTIVE;
- Growth Balance is positive;
- destination URL is real;
- authorization and transaction webhooks are healthy;
- pause/resume remains fail-closed.

Then run the existing dogfood harness with a deliberately small owner-operated amount. Product proof remains at least one real PAID conversion with calculable CAC.

## Operational limitation

Checkout-only Growth Balance is an MVP bridge, not the final money-moving architecture. Issue #160 is the durable reminder and release criterion for autonomous Partizan-funded advertising spend.
