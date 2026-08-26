# Growth AutoResearch

Roadmap: GitHub issue #179. Phase 1 implementation task: #180.

## Purpose

Growth AutoResearch turns Partizan's existing research, experiment, analytics and learning pipeline into a controlled champion/challenger loop.

The customer-level loop is intentionally different from Karpathy's code-editing setup: customer agents do **not** edit Partizan production code. They operate on a bounded `GrowthVariantSpec` and remain behind existing Growth Mandate, channel, integration, provider, approval and spend controls.

## Phase 1: shadow mode

Phase 1 provides the research-domain foundation only:

```text
GrowthResearchPolicy
  -> current GrowthChampion
  -> bounded GrowthVariantSpec challenger
  -> GrowthResearchTrial
  -> deterministic shadow evaluation
  -> KEEP / DISCARD / INCONCLUSIVE / BLOCKED / FAILED
  -> champion promotion only on KEEP
  -> persisted history
```

No external provider is mutated and no real spend is authorized by this module.

### Bounded variant surface

A variant may describe approved growth dimensions such as platform, tactic, audience, message angle, offer, creative reference, CTA, destination, targeting, timing and a shadow test budget.

The policy controls:

- allowed platforms;
- maximum changed dimensions per challenger (1-2);
- maximum shadow trial budget;
- minimum paid-user evidence;
- minimum material CAC improvement;
- pause state.

### Stale-trial safety

Every trial is anchored to the champion that existed when the challenger was created. If another trial promotes a new champion first, the stale trial is evaluated as `BLOCKED` and cannot overwrite the newer champion.

### Evaluation scope

The Phase 1 evaluator is deliberately simple and deterministic. It uses minimum paid-user evidence and a material CAC-improvement band so the controller has reproducible keep/discard/inconclusive behavior.

The richer business-objective hierarchy, confidence/uncertainty model and comparable real-test budgets belong to Phase 2 of #179.

## Operator API

Phase 1 exposes internal/operator routes:

- `PUT /products/{product_id}/growth-autoresearch/policy`
- `POST /products/{product_id}/growth-autoresearch/baseline`
- `POST /products/{product_id}/growth-autoresearch/trials`
- `POST /growth-autoresearch/trials/{trial_id}/evaluate`
- `GET /products/{product_id}/growth-autoresearch`

These routes inherit the existing control-plane operator boundary. Customer workspace exposure is a later roadmap phase.

## Hard boundaries

Phase 1 must not:

- mutate Meta/TikTok/Telegram or any external provider;
- activate or increase paid spend;
- bypass Growth Mandate or channel permissions;
- imply that research access is execution integration;
- generate autonomous hypotheses through an LLM yet.

Hypothesis generation is Phase 3. Paid AutoResearch stays blocked until the spend rail tracked in #160 is genuinely settlement-ready.
