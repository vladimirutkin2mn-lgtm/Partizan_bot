# AutoResearch Phase 5 — permissioned non-paid execution

Phase 5 allows an existing READY Growth AutoResearch challenger to execute only through Partizan's existing permissioned distribution control plane.

## Invariants

- Phase 5 is non-paid only. `PAID_CAMPAIGN` / `PAID_PLATFORM` execution is blocked and proposed spend must remain `0`.
- A customer channel must already be enabled for autonomous execution. Research-only or disabled channel state does not grant provider execution access.
- Growth Mandate, identity/campaign-slot selection, drafting, approval and adapter checks remain authoritative.
- Missing integration, permission, adapter, identity or readiness produces a blocked/unavailable result rather than synthetic success.
- The AutoResearch hypothesis generator never mutates a provider directly.
- Successful execution creates auditable DistributionAction / DistributionExperiment linkage but does not itself promote a GrowthChampion. Promotion still requires downstream evidence and the research evaluator.
- Pause is non-terminal: READY trial/linkage state is retained for later resume.
- Worker and operator execution share the autonomous-growth advisory lock so two processes cannot execute the same READY trial concurrently.
- A PREPARING reservation is persisted before action creation. Interrupted preparation is fail-closed after restart to avoid blindly creating a duplicate external action.
- Reconciliation resumes only from the already-created action linkage; it must not recreate the action.

## Paid boundary

Paid AutoResearch remains out of scope until issue #160 makes the Growth Balance spend rail genuinely settlement-ready. Funding Growth Balance through Stripe Checkout alone is not authorization for autonomous paid provider mutation.

This document describes the safety contract only; it does not widen any execution permission or integration capability.
