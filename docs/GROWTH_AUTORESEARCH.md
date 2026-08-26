# Growth AutoResearch

Roadmap: GitHub issue #179. Phase 1 implementation task: #180. Phase 2: #186. Phase 3: #192.

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
- research-budget share per challenger;
- maximum comparable trial duration;
- minimum paid, activation, signup and traffic evidence;
- CAC and proxy materiality thresholds;
- confidence threshold;
- ROAS regression guardrail;
- pause state.

### Stale-trial safety

Every trial is anchored to the champion that existed when the challenger was created. If another trial promotes a new champion first, the stale trial is evaluated as `BLOCKED` and cannot overwrite the newer champion.

## Phase 2: fixed business evaluation harness

Phase 2 defines Partizan's equivalent of autoresearch's fixed evaluation metric. Marketing cannot safely optimize CTR as the winning objective, so evaluation uses the highest common decision-grade downstream objective available for both champion and challenger.

Priority order:

1. `PAID_CAC` when both variants have enough paid users and measurable spend;
2. `PAID_CONVERSION` when paid evidence is decision-grade but CAC is not comparable;
3. `ACTIVATION_CONVERSION` when purchase economics are not decision-grade;
4. `SIGNUP_CONVERSION` when activation evidence is not decision-grade;
5. `NONE` / `INCONCLUSIVE` when only engagement diagnostics such as CTR remain.

CTR and clicks are stored as diagnostics only. They can never independently promote a challenger.

### Materiality and confidence

A challenger must clear both:

- the configured minimum material improvement; and
- the configured confidence threshold.

Paid-CAC confidence uses the observed customer-acquisition rate per unit of spend. Conversion proxy confidence uses the observed difference in visit-to-event conversion rates. The calculations are deterministic for a fixed evidence snapshot.

A strong point estimate with weak evidence remains `INCONCLUSIVE`. One paid conversion is not decision-grade by default.

### ROAS guardrail

CAC remains the primary paid acquisition objective, but a CAC winner is not promoted when its ROAS regresses beyond the configured guardrail. Conflicting business signals remain `INCONCLUSIVE` rather than being optimized away.

### Comparable trial protocol

Shadow challengers are bounded by:

- maximum planned spend;
- optional maximum share of a research budget;
- maximum duration;
- minimum downstream evidence and traffic.

Evidence that exceeds the trial protocol fails closed as `FAILED`; it is not silently compared with a differently-sized test.

## Phase 3: bounded hypothesis generator

Phase 3 lets Partizan propose the next challenger instead of requiring an operator to write every `GrowthVariantSpec` manually.

The generator receives:

- Product Intake / project facts;
- current champion and evidence;
- recent AutoResearch trials and outcomes;
- Distribution Growth Manager learning memory;
- ready Distribution Plays and concrete opportunities when available;
- policy-allowed platforms;
- remaining shadow research budget.

It produces exactly one structured hypothesis containing a proposed variant, a human-readable rationale and an explicit `EXPLOIT` or `EXPLORE` intent.

### Explore vs exploit

`EXPLOIT` makes a small variation around a promising current pattern, normally one changed dimension such as message angle, audience or offer.

`EXPLORE` deliberately tests a meaningfully different bounded surface such as another allowed platform, tactic or audience. `AUTO` selects between the two using recent research history while still allowing an operator to request either mode explicitly.

### Validation boundary

The LLM is a proposer, never an authority. Every generated draft is passed through the same `GrowthAutoResearchService.create_challenger` validator used by manual trials. A prompt cannot grant a platform, budget or changed-dimension permission that the policy does not allow.

When an LLM draft violates policy, the rejection is fed back for regeneration. After bounded retries, the system uses a deterministic sandbox fallback rather than weakening the validator.

### Duplicate suppression

Exact and near-duplicate variants that previously ended as `DISCARD` or `FAILED` are rejected before trial creation. Cosmetic rewrites of a losing hypothesis therefore do not consume the next research slot.

### Deterministic fallback

When `LLM_PROVIDER=mock`, Phase 3 still produces reproducible bounded hypotheses from product facts, current champion, prior trials, allowed platforms and remaining budget. This keeps the Growth Sandbox and CI independent from a live model API.

Hypothesis text, rationale, mode and source are persisted with the generated trial so later evaluation and workspace UX can explain what Partizan tested and why.

## Operator API

The internal/operator routes are:

- `PUT /products/{product_id}/growth-autoresearch/policy`
- `POST /products/{product_id}/growth-autoresearch/baseline`
- `POST /products/{product_id}/growth-autoresearch/hypotheses`
- `POST /products/{product_id}/growth-autoresearch/trials`
- `POST /growth-autoresearch/trials/{trial_id}/evaluate`
- `GET /products/{product_id}/growth-autoresearch`

These routes inherit the existing control-plane operator boundary. Customer workspace exposure is a later roadmap phase.

## Hard boundaries

Current phases must not:

- mutate Meta/TikTok/Telegram or any external provider;
- activate or increase paid spend;
- bypass Growth Mandate or channel permissions;
- imply that research access is execution integration;
- let LLM-generated hypotheses bypass deterministic policy validation.

Phase 4 adds a continuous research-only loop and customer workspace visibility. Paid AutoResearch stays blocked until the spend rail tracked in #160 is genuinely settlement-ready.
