# Generic Partizan growth runner

`partizan-growth-run` is the product-agnostic CLI for traversing the Partizan acquisition pipeline. Oracle-specific dogfood remains a compatibility preset only; new products should use this runner.

## 1. Start from an existing confirmed Partizan product

```bash
partizan-growth-run \
  --base-url https://partizan.example.com \
  --product-id <PRODUCT_UUID> \
  --destination-url https://your-product.example \
  --platform INSTAGRAM \
  --tactic-class PAID_PLATFORM
```

This mode does not recreate or reinterpret the product brief. The ProductProfile must already be `CONFIRMED`.

## 2. Start from a free-text product brief

Put the product facts, business goal, budget and constraints in a file:

```text
Product: Example AI app
Description: ...
Problem: ...
Value proposition: ...
Market: US
Language: English
Price: $9.99/month
Budget: $500
Max CAC: $12
Goal: Acquire first 50 paid users
Allowed channels: creator outreach, paid social, owned content
```

Then run:

```bash
partizan-growth-run \
  --brief-file product.txt \
  --destination-url https://your-product.example
```

If Product Intake needs a material clarification, the runner stops instead of guessing. The blocker tells you the exact field to supply:

```text
Missing explicit clarification answer. Re-run with --answer 'market=<value>' for: Which market?
```

Then rerun with an explicit answer:

```bash
partizan-growth-run \
  --brief-file product.txt \
  --answer market=US \
  --answer language=English \
  --destination-url https://your-product.example
```

`--brief` can be used instead of `--brief-file` for short briefs. `--product-id`, `--brief` and `--brief-file` are mutually exclusive.

## 3. What the runner does

```text
/health/ready
  -> create or load confirmed ProductProfile
  -> generate ICPs
  -> discover concrete distribution opportunities
  -> attempt bounded enrichment
  -> generate Distribution Plays
  -> select highest-priority READY play matching optional filters
  -> auto-prepare one DistributionAction + DistributionExperiment
  -> print tracking/referral attribution
  -> inspect integration readiness, analytics and learning
```

If setup objects are missing, the runner prints concrete blockers rather than fabricating identities, policies or provider configuration.

## 4. Dry-run is the default

Without `--execute`, the runner stops after preparing the action/experiment.

```bash
partizan-growth-run \
  --product-id <PRODUCT_UUID> \
  --destination-url https://your-product.example
```

Use `--json` for a machine-readable final report.

## 5. Explicit external execution

`--execute` calls only the existing Partizan approval + execution-adapter boundary:

```bash
export PARTIZAN_OPERATOR_KEY='<deployment secret>'
partizan-growth-run \
  --product-id <PRODUCT_UUID> \
  --destination-url https://your-product.example \
  --execute
```

There is intentionally no CLI argument for operator/provider secrets. Production secrets stay in environment/deployment secret stores.

### Paid-spend boundary

For paid actions the runner may create provider-side objects through the existing adapter, but `STAGED` remains a stopping point. The runner never calls:

- paid activation authorization;
- paid activation;
- budget increase/update;
- restart/re-enable of paused spend.

Those remain separate exact-budget control-plane actions.

## 6. Connecting conversions

Before a real growth run, use the Product Integration Kit in `/app`:

1. create a Product Event Key;
2. inspect `Проверка интеграции`;
3. copy the product-specific cURL/Python/Node integration guide;
4. send one representative payload to `/distribution-events/verify`;
5. enable real backend delivery to `/distribution-events`;
6. confirm real VISIT/SIGNUP/ACTIVATED/PAID observations.

The runner reads integration status but does not generate synthetic conversions.

## 7. Repository boundary

The generic runner operates against the Partizan API. It does not modify the product repository, deploy the product, or migrate the product database. If product-side integration work is required, that work belongs to the product owner unless explicit permission is separately granted for that external project/action.
