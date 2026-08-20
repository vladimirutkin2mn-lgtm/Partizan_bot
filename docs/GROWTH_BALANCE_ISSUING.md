# Growth Balance — Stripe Issuing production rail

This runbook enables the Partizan-funded acquisition rail behind Growth Balance.

The product contract is:

- the customer funds one prepaid Growth Balance;
- autonomous execution has no monthly subscription;
- Growth Balance pays actual acquisition/media spend plus Partizan's variable fee;
- the variable fee is 10% of actual acquisition spend;
- funding Growth Balance includes the acquisition research required for execution;
- the customer does not provide a billing card for Partizan-managed Meta spend;
- Partizan never stores or returns Issuing PAN/CVC.

## Architecture

Partizan uses a pre-funded Stripe Issuing liquidity pool and creates one Partizan-owned virtual card per customer project.

Each project card is:

- virtual;
- USD-only for the first production version;
- restricted to Stripe merchant category `advertising_services`;
- capped by an all-time spending limit equal to that project's Growth Balance acquisition capacity;
- created inactive;
- activated only after the exact Meta ad account is connected and the Partizan-funded card is confirmed as that account's billing rail.

Stripe Issuing captures and refunds are the financial source of truth for Growth Balance acquisition spend. Meta analytics remain useful for campaign performance, but they do not decide how much money has left the Growth Balance.

## 1. Enable and pre-fund Stripe Issuing

Before changing Partizan configuration:

1. Enable Stripe Issuing on the Partizan Stripe account and complete any Stripe account/cardholder requirements.
2. Create or choose a Partizan company Issuing Cardholder.
3. Pre-fund the Stripe Issuing balance with enough USD liquidity for the customer acquisition commitments Partizan intends to accept.
4. Keep `GROWTH_BALANCE_SETTLEMENT_PROVIDER=unavailable` until every step in this runbook is complete.

Partizan checks live `balance.issuing.available` before opening each Growth Balance Checkout. A new top-up is rejected when the pre-funded Issuing liquidity cannot cover existing outstanding acquisition capacity plus the new incremental capacity.

## 2. Configure production secrets

Set these values in `.env.prod` / deployment secrets:

```dotenv
GROWTH_BALANCE_SETTLEMENT_PROVIDER=stripe_issuing
STRIPE_ISSUING_CARDHOLDER_ID=ich_...
STRIPE_ISSUING_CURRENCY=usd
STRIPE_ISSUING_AUTHORIZATION_WEBHOOK_SECRET=whsec_...
STRIPE_ISSUING_EVENTS_WEBHOOK_SECRET=whsec_...
STRIPE_ISSUING_WEBHOOK_API_VERSION=2025-03-31.basil
```

The existing Stripe settings are still required:

```dotenv
STRIPE_SECRET_KEY=sk_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_LAUNCH_PRICE_ID=price_...
```

`STRIPE_LAUNCH_PRICE_ID` is only the optional `$49` Acquisition Plan Price. Growth Balance has no recurring Autopilot Price.

Do not copy card numbers, CVCs, or card expiry values into Partizan environment variables, logs, the database, GitHub, or this runbook.

## 3. Configure the synchronous authorization webhook

Create a dedicated Stripe Issuing authorization webhook endpoint:

```text
https://<partizan-host>/v1/billing/stripe/issuing-authorizations
```

Subscribe it to the synchronous Issuing authorization request event used by Stripe for direct authorization decisions.

Store that endpoint's signing secret in:

```text
STRIPE_ISSUING_AUTHORIZATION_WEBHOOK_SECRET
```

Partizan returns a direct approve/decline decision. Approval requires all of the following at authorization time:

- the card belongs to a known Partizan Growth Balance rail;
- the card is bound to the exact customer Meta ad account;
- the card is active;
- currency is USD;
- merchant category is `advertising_services`;
- the request fits inside remaining project acquisition capacity;
- the Product has an ACTIVE Growth Mandate.

The card-level Stripe MCC and all-time amount controls are a second independent boundary.

## 4. Configure the financial transaction webhook

Create a second Stripe webhook endpoint:

```text
https://<partizan-host>/v1/billing/stripe/issuing-events
```

Subscribe it to:

- `issuing_transaction.created`
- `issuing_transaction.updated`

Store that endpoint's signing secret in:

```text
STRIPE_ISSUING_EVENTS_WEBHOOK_SECRET
```

Partizan stores only non-sensitive transaction accounting data. Captures increase acquisition spend; refunds decrease it. Re-delivery is idempotent by Stripe Issuing transaction ID.

An unexpected captured merchant category or unexpected currency pauses the project card fail-closed.

## 5. Run production preflight

Before deployment:

```bash
PARTIZAN_REQUIRE_PUBLIC_URL=true bash tools/preflight_prod_host.sh .env.prod
```

When `GROWTH_BALANCE_SETTLEMENT_PROVIDER=stripe_issuing`, preflight fails unless the public HTTPS origin, Stripe key, Issuing Cardholder ID, both webhook secrets, USD currency, and explicit Stripe API version are configured.

Do not bypass preflight to test a partially configured money-moving rail.

## 6. Customer funds Growth Balance

The customer can choose autonomous execution directly after the free pre-scan. No subscription purchase is required.

Before Stripe Checkout opens, Partizan:

1. obtains a cross-process liquidity allocation lock;
2. checks all existing Growth Balance commitments;
3. reads current Stripe Issuing available USD;
4. reserves acquisition capacity in the Partizan ledger;
5. only then creates Stripe Checkout.

Growth Balance Checkout expires after 30 minutes. The Partizan liquidity reservation is held for 31 minutes to cover delivery/timing skew.

When payment completes, Partizan credits the exact paid amount, grants the internal acquisition-research entitlement, and provisions or raises the project virtual-card limit. If Checkout was created but the following session-id persistence write failed, the signed paid event can recover the payment from the pre-Stripe liquidity reservation instead of accepting customer money without crediting Growth Balance.

## 7. Bind the Partizan card to the exact Meta ad account

The current MVP does **not** automate adding the Partizan Issuing card as a Meta billing payment method.

Use the approved Stripe secure card-detail surface and Meta billing UI/process to attach the Partizan project card to the exact Meta ad account. Do not copy PAN/CVC through Partizan APIs or persistence.

After provider-side attachment is complete, record the binding in Partizan with the protected operator endpoint:

```bash
curl -fsS -X POST \
  "$PARTIZAN_PUBLIC_BASE_URL/v1/customer-projects/$PROJECT_ID/growth-balance/rail/meta-binding" \
  -H "X-Partizan-Operator-Key: $OPERATOR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "ad_account_id": "act_123456789",
    "confirm_partizan_card_primary": true,
    "confirm_customer_payment_method_not_used": true
  }'
```

Partizan verifies that `ad_account_id` exactly matches the customer's connected Meta provider configuration. The endpoint does not accept card credentials.

Do not confirm the binding while the customer-owned payment method can still be charged for Partizan-managed spend.

## 8. Activate Autopilot

A customer Resume / activation succeeds only when all product safety gates pass and:

- Growth Balance has acquisition capacity;
- acquisition research is complete;
- Stripe Issuing settlement is ready;
- Meta is connected;
- the project card is bound to that Meta account;
- the customer has explicitly authorized autonomous paid experiments inside the configured guardrails.

On customer Pause or another Partizan safety stop, the Growth Mandate is closed first and the Issuing card is then set inactive. The real-time Issuing authorization endpoint independently checks the ACTIVE mandate, so the software guardrail remains fail-closed even if a card status API call temporarily fails.

## 9. Reconciliation

For customer-facing Growth Balance:

```text
actual acquisition spend = net Stripe Issuing captures/refunds
Partizan variable fee     = 10% × actual acquisition spend
Growth Balance used       = acquisition spend + variable fee
Growth Balance available  = funded amount - used amount
```

Do not substitute Meta-reported spend for the financial ledger.

## 10. First live dogfood

Start with an owner-operated project and a deliberately small funded amount.

Before the first live sweep verify:

- Stripe Issuing is enabled and pre-funded;
- the project card exists and is advertising-only;
- the exact Meta ad account is bound;
- Growth Mandate is ACTIVE;
- Growth Balance is positive;
- destination URL is real;
- the synchronous authorization webhook is healthy;
- the Issuing transaction webhook is healthy.

Then run the existing dogfood harness. Do not bypass Growth Mandate, provider staging, paid-control, reconciliation, or conversion attribution.

A successful technical setup is not yet product proof. Dogfood proof remains at least one real PAID conversion with calculable CAC.

## Operational limitations

- Initial self-service paid provider remains Meta only.
- Provider-side attachment of the Partizan project card to Meta billing is operator-assisted for now.
- Stripe Issuing account/region eligibility and the amount of pre-funded liquidity are external production prerequisites.
- This implementation does not move Payments funds into Issuing automatically; production uses a deliberately pre-funded Issuing liquidity pool.
- No real customer funds or ad spend are required to validate the repository implementation and CI.
