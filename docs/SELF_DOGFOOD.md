# Partizan self-dogfood

This is the first-party dogfood path where **Partizan uses Partizan to acquire customers for Partizan**.

It exists to make Milestone #10 testable without modifying Numa/Globa or another external product.

## What is automatic

Once a real Partizan acquisition product/experiment is configured:

1. a user clicks the experiment's normal Partizan tracking URL;
2. `/r/{referral_token}` records the real `VISIT`;
3. if the experiment belongs to the configured self-dogfood product and redirects back to the Partizan public origin, Partizan stores a first-click HttpOnly referral cookie;
4. the next Product Understanding customer project is bound to that referral;
5. account registration records `SIGNUP`;
6. the first authenticated customer workspace use records `ACTIVATED`;
7. a verified real Stripe Acquisition Plan purchase records `PAID` with the actual checkout revenue.

The same deterministic event ID is reused when the browser recovery path and Stripe webhook observe the same purchase, so retries do not create duplicate paid conversions.

## What is deliberately not counted

Funding an acquisition budget / Growth Balance is **not** Partizan revenue and is not recorded as a `PAID` conversion for self-dogfood.

A paid conversion is the real sale of the Acquisition Plan. Future management-fee revenue can be added as a separate business event only when the paid-spend rail and fee settlement are genuinely live.

## Configuration

Create and research a Product representing Partizan itself, then set its UUID in production. This configures attribution only; it does not enable paid execution:

```env
PARTIZAN_SELF_DOGFOOD_PRODUCT_ID=<partizan-product-uuid>
```

`PARTIZAN_PUBLIC_BASE_URL` must already point at the real Partizan HTTPS origin.

The self-dogfood cookie is captured only when all of these are true:

- the configured Product ID matches the experiment Product;
- the experiment is `RUNNING` or `FINISHED`;
- the redirect destination has the same origin as `PARTIZAN_PUBLIC_BASE_URL`;
- no earlier self-dogfood first-click cookie exists.

A request cannot choose an arbitrary Product ID for this flow.

## Run the experiment

Use the existing Partizan acquisition pipeline and `partizan-growth-run`.

For the first proof, prefer a permissioned non-paid or assisted path so #160 is not a blocker.

The destination should be the real Partizan landing/onboarding path, for example:

```text
https://partizanlabs.com/start
```

Partizan's normal tracking link remains the public entry point:

```text
https://partizanlabs.com/r/{referral_token}
```

Do not post or mutate an external channel until that action is explicitly authorized.

## Readiness / proof snapshot

Inside the production runtime:

```bash
partizan-self-dogfood
```

The command is read-only and prints no credentials. It reports:

- configured self-dogfood Product;
- experiment count and measurable/running experiments;
- `VISIT / SIGNUP / ACTIVATED / PAID`;
- spend, revenue, CAC and ROAS;
- Growth Manager learning count and latest decision;
- remaining blockers.

To keep a production checklist red until the full real proof exists:

```bash
partizan-self-dogfood --require-proof
```

The command exits non-zero until Partizan has:

- a real tracked visit;
- a real signup;
- a real activated workspace user;
- a real paid Acquisition Plan conversion;
- real experiment spend;
- calculable CAC;
- at least one Growth Manager learning entry.

## Milestone #10 boundary

A green unit/integration test does **not** close #10.

A green `partizan-self-dogfood --require-proof` in production also needs to represent real external users and real acquisition activity, not manually inserted production events.

The final proof must show that the real economics feed the Growth Manager and influence what Partizan chooses to test next.
