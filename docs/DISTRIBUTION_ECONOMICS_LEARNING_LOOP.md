# Distribution economics and learning loop

Phase 7 extends the existing distribution analytics and Growth Manager loop. It does not introduce a second decision engine.

## Cost model

Every distribution spend fact has a category and an evidence kind.

Cost categories:

- `RESEARCH_FEE` — customer-facing research/discovery fee.
- `EXECUTION_FEE` — customer-facing execution or management fee.
- `DISTRIBUTION_SPEND` — media, placement or other direct distribution spend.
- `OPERATING_COST` — Partizan's internal fulfillment cost. This is not exposed in customer economics.

Evidence kinds:

- `OBSERVED` — measured or reconciled real cost.
- `ESTIMATE` — planning estimate.
- `SYNTHETIC` — test/simulation evidence.

Legacy `/distribution-experiments/{experiment_id}/spend` calls that omit the new fields remain backward compatible and are interpreted as `DISTRIBUTION_SPEND` + `OBSERVED`.

Only `OBSERVED` cost facts participate in experiment/product economics, CAC/ROAS, customer totals and Growth Manager decisions. `ESTIMATE` and `SYNTHETIC` facts remain durably stored as evidence, but cannot change measured economics or decisions.

Customer CAC/ROAS use observed customer-visible cost (`RESEARCH_FEE + EXECUTION_FEE + DISTRIBUTION_SPEND`). Internal `OPERATING_COST` is tracked separately and cannot leak through the customer economics response.

Optional `publisher_mode` and `action_type` values supplied with spend ingestion are assertions against the actual `DistributionAction` provenance. A mismatch is rejected rather than allowing analytics provenance to be rewritten.

Fulfilled `PARTIZAN_MANAGED` assignments are reused directly as cost evidence: `distribution_spend_usd` maps to distribution spend, `management_fee_usd` to execution fee and `operational_cost_usd` to internal operating cost. The analytics layer does not require a duplicate spend record for those facts.

## Pricing assumptions

`GET /v1/products/{product_id}/distribution-pricing-assumptions` derives operating-cost assumptions only from real `OBSERVED` operating-cost samples and fulfilled managed assignments. `ESTIMATE` and `SYNTHETIC` operating-cost entries are deliberately excluded.

This prevents test fixtures and planning guesses from silently becoming production pricing assumptions.

## Outcome model

Distribution analytics accepts the existing funnel outcomes plus community execution outcomes:

- `VISIT`
- `SIGNUP`
- `ACTIVATED`
- `PAID`
- `REPLY`
- `REMOVED`

Reddit/Telegram observation histories are also normalized into experiment analytics where available. Reply counts are positive engagement evidence. A confirmed removal is treated as a hard negative signal for the exact community/action pattern.

## Publisher-mode comparisons

Analytics expose breakdowns by:

- platform;
- tactic;
- distribution identity;
- publisher mode (`MANUAL`, `CLIENT_OWNED`, `PARTIZAN_MANAGED`);
- action type;
- opportunity/community.

Publisher mode is inferred from durable execution provenance: managed fulfillment observation, confirmed client-owned provider receipt, or manual fallback.

## Decisions and durable learning

The existing Growth Manager remains the source of `STOP / CONTINUE / MODIFY / SCALE` decisions.

Phase 7 adds publisher mode, action type, replies and removals to the decision fingerprint and durable learning entry. This means new observations create a new decision instead of being hidden by the prior idempotency fingerprint.

A removal forces `STOP` for the exact observed community/action execution pattern. Positive reply evidence can improve future portfolio scoring. Reddit opportunity enrichment also feeds these durable outcomes back into its prior-outcome score: replies can improve research ranking while removals apply a strong penalty.

## Customer reporting

`GET /customer/workspace/{project_id}/distribution-economics` is account/session scoped and returns only customer-facing economics:

- research fee;
- execution/management fee;
- distribution spend;
- total customer cost;
- paid users;
- revenue;
- CAC;
- ROAS.

Internal operating cost is intentionally absent from this response.

## Production acceptance boundary

This code can be verified with synthetic fixtures, but Phase 7 must not be closed using synthetic evidence alone.

Before closing issue #255, at least one real end-to-end customer experiment must produce persisted execution/outcome/cost evidence and a measured next `STOP / CONTINUE / MODIFY / SCALE` decision. Pricing assumptions intended for production use must come from real observed operating cost.

The shared-host TLS incident #266 is a separate infrastructure blocker; it does not change the Phase 7 data/decision contract, but production end-to-end acceptance remains dependent on a healthy public edge.
