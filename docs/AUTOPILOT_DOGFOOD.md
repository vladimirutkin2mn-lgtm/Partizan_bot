# Autopilot dogfood

This is the production proof path for customer-facing autonomous acquisition.

The goal is not a synthetic green run. Dogfood is complete only when one real customer project has:

- a real website or landing page recorded as the product's paid-traffic destination;
- a funded Growth Balance with remaining acquisition capacity;
- a bounded ACTIVE Growth Mandate;
- a self-service Meta connection;
- a ready Partizan-funded provider payment rail;
- at least one real PAID conversion attributed into Partizan;
- a calculable CAC from observed spend and paid conversions.

There is no recurring Autopilot subscription gate.

## Safe readiness check

Run the command inside the production Partizan runtime. Pass customer access only through environment variables so the customer token does not appear in shell history or process arguments:

```bash
export PARTIZAN_DOGFOOD_PROJECT_ID='<customer-project-uuid>'
export PARTIZAN_DOGFOOD_CUSTOMER_TOKEN='<customer-project-token>'
partizan-autopilot-dogfood
```

The default command does not launch a campaign. It checks the existing customer execution state plus live-production prerequisites:

- `APP_ENV=production`;
- database runtime storage;
- public HTTPS origin;
- a researched Product with a real `reference_links` destination for tracked traffic;
- correctly configured Stripe Acquisition Plan Price for the optional `$49` report path;
- ACTIVE Growth Mandate;
- Meta connection;
- remaining Growth Balance acquisition capacity;
- ready Partizan-funded settlement rail;
- positive target max CAC;
- live OpenAI image creative provider.

The customer token is never included in the output.

## Customer destination

The `/start` onboarding asks for a website or landing page. The customer project persists that URL and Product Intake carries it into `Product.reference_links`. The existing execution engine then uses that link as the base destination for Partizan tracking and the Meta campaign.

The customer API keeps `website_url` optional for backwards compatibility with older Acquisition Plan-only projects. Such a project can still view its plan, but paid activation fails closed until the researched Product has a destination URL.

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

This runner does not create the optional Stripe Acquisition Plan Price, Meta developer apps, Meta billing methods or customer ad accounts. Those are external account-owner setup steps. Growth Balance fee accounting comes from actual acquisition settlement rather than a separate recurring subscription.
