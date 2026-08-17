# Autopilot dogfood

This is the production proof path for the customer-facing Autopilot.

The goal is not a synthetic green run. Dogfood is complete only when one real customer project has:

- an ACTIVE Autopilot subscription;
- a bounded ACTIVE Growth Mandate;
- a self-service Meta connection;
- at least one real PAID conversion attributed into Partizan;
- a calculable CAC from observed spend and paid conversions.

## Safe readiness check

Run the command inside the production Partizan runtime. Pass customer access only through environment variables so the customer token does not appear in shell history or process arguments:

```bash
export PARTIZAN_DOGFOOD_PROJECT_ID='<customer-project-uuid>'
export PARTIZAN_DOGFOOD_CUSTOMER_TOKEN='<customer-project-token>'
partizan-autopilot-dogfood
```

The default command does not launch a campaign. It checks the existing customer Autopilot state plus live-production prerequisites:

- `APP_ENV=production`;
- database runtime storage;
- public HTTPS origin;
- active and correctly configured Stripe Acquisition Plan and Autopilot Prices;
- ACTIVE customer subscription and Growth Mandate;
- Meta connection;
- remaining delegated marketing budget;
- positive target max CAC;
- live OpenAI image creative provider.

The customer token is never included in the output.

## Run exactly one bounded live sweep

A real paid sweep is deliberately harder to invoke than readiness:

```bash
partizan-autopilot-dogfood \
  --run-one-sweep \
  --confirm-live-spend RUN_ONE_LIVE_PAID_SWEEP
```

The command re-runs all readiness gates before invoking the existing `AutonomousGrowthWorker` for exactly one product-scoped sweep. It does not create a second execution path and does not bypass Growth Mandate evaluation, creative readiness, provider staging, autonomous paid activation, paid-control, reconciliation or analytics.

If any prerequisite is missing, no live sweep is invoked.

## Completion gate

Use this in a dogfood checklist or deployment shell when the proof must remain red until a real customer is acquired:

```bash
partizan-autopilot-dogfood --require-paid-conversion
```

It exits non-zero until `paid_customers >= 1` and CAC is calculable.

## Meta creative boundary

Customer Meta OAuth does not require a static image URL. Autonomous paid growth first generates or reuses an action-level provider-ready `CreativeAsset`. Meta staging uses that exact asset URL. The old connection-level `default_image_url` remains a compatibility fallback for operator-created connections only.

If neither an action-level public image nor a legacy fallback exists, Meta staging fails closed before any provider campaign objects are created.

## What this does not automate

This runner does not create Stripe products/prices, Meta developer apps, Meta billing methods or customer ad accounts. Those are external account-owner setup steps. It also does not automatically invoice Partizan's managed-spend fee; that remains a separate billing milestone.
