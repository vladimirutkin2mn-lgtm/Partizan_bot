# Marketing Intelligence

Partizan's Marketing Intelligence layer improves the quality of marketing reasoning without changing the execution control plane.

The current version is a curated, version-pinned adaptation of selected methodology from [`coreyhaines31/marketingskills`](https://github.com/coreyhaines31/marketingskills), pinned to commit `7868cb9251fad80a73d26e488a5ad5f6c4a9f335` and used under the upstream MIT license.

## Why this exists

Partizan already owns the durable acquisition loop:

```text
ProductProfile
  -> ranked ICPs
  -> Distribution Opportunities
  -> Distribution Plays
  -> permissioned execution
  -> attribution / CAC / ROAS
  -> Growth Manager
  -> learning memory
```

Marketing Intelligence is deliberately narrower. It supplies task-specific methodology and quality checks so that Partizan makes better marketing judgments before its existing schemas, policies and execution boundaries take over.

It is not a second agent runtime and it does not execute anything by itself.

## Authority boundary

Marketing skills are **reasoning guidance only**.

They cannot override:

- founder-confirmed product facts;
- Partizan system prompts and structured response schemas;
- `DistributionExecutionPolicy` or `CommunityPolicy`;
- user approvals, mandates or delegations;
- paid spend authorization and budget caps;
- outreach send limits, suppression or contact provenance requirements;
- provider or platform constraints.

Partizan never fetches the upstream skill repository at runtime. The curated guidance is stored locally and pinned to a known upstream revision so a future upstream edit cannot silently change production behavior.

## Curated skill packs

| Skill pack | Pinned version | Intended Partizan use |
|---|---:|---|
| `product-marketing` | 2.1.0 | Product understanding, positioning, differentiation |
| `customer-research` | 2.0.1 | JTBD, pains, triggers, customer evidence discipline |
| `prospecting` | 1.1.0 | Demand signals, qualification, provenance |
| `community-marketing` | 2.0.0 | Value-first community participation |
| `influencer-marketing` | 1.0.0 | Creator fit and creator briefs |
| `marketing-ideas` | 2.0.0 | Growth-play selection and sequencing |
| `cold-email` | 2.0.0 | Bounded creator/partner outreach drafting |
| `ad-creative` | 2.8.0 | Paid creative angles and grounded claims |

The registry contains the whole initial set, but registration does not imply that every skill is invoked through an LLM. Where a methodology is better expressed as deterministic scoring or policy, Partizan implements it directly.

## Runtime integrations

### Product Intake

`ProductIntakeAgent` receives `product-marketing + customer-research` guidance.

The existing founder-source-of-truth rule remains authoritative. Marketing Intelligence encourages better extraction of the customer job, problem, desired outcome, alternatives and differentiation, but it cannot turn assumptions into product facts.

### ICP generation

`ICPEngine` receives `product-marketing + customer-research + prospecting` guidance.

The prompt explicitly distinguishes an ICP hypothesis from observed customer evidence. When no external customer evidence is available, the model must not describe demand, willingness to pay, market size or personas as validated facts. ICP scores remain prioritization hypotheses for experiments.

### Audience Intelligence

`AudienceIntelligenceEngine` applies the `customer-research + prospecting + community-marketing` methodology as deterministic evidence scoring.

The important evidence boundary is explicit: **the search query is provenance, not evidence**. ICP words placed into a discovery query cannot increase an opportunity score merely because the search provider returns that query back to Partizan. Only observed source text from a result title and snippet contributes to evidence scoring.

Each candidate is scored from observable signals:

- ICP/context fit;
- pain-language overlap;
- trigger-language overlap;
- alternative-language overlap;
- demand-intent markers such as looking for help, recommendations or alternatives;
- commercial-intent markers such as price, trial, subscription or purchase language;
- number of independent evidence URLs.

The opportunity persists a `research_signals` block with the component ratios, intent-hit counts, evidence count, independent evidence count, matched terms, observed signal tags and a `LOW` / `MEDIUM` / `HIGH` confidence label. Individual evidence records also persist signal tags and source class.

This integration adds **no extra LLM calls and no extra discovery/search requests**. The same retrieved evidence is evaluated more rigorously and reproducibly.

### Distribution action drafting

`DistributionActionComposer` selects guidance by the already-authorized action type:

- `PAID_CAMPAIGN` -> `ad-creative + customer-research + product-marketing`;
- `ORGANIC_VIDEO` -> `influencer-marketing + ad-creative + customer-research`;
- `COMMENT`, `REPLY`, `STANDALONE_POST` -> `community-marketing + customer-research`.

The pre-existing Partizan action-drafting rules remain first: no impersonation, no fabricated experience or results, no generic spam, no cold DM through this surface, and no community promotion beyond the applied policy.

### Bounded outreach

`OutreachBriefComposer` receives `cold-email + prospecting + product-marketing` guidance.

This changes only how an already-permitted outreach draft is written. It encourages recipient-first language, evidence-linked personalization, one low-friction ask and concise human copy. It does **not** make outreach less bounded.

Before the composer is reached, the existing outreach runtime still requires a concrete `OutreachTarget`, an evidenced business contact and a non-suppressed recipient. After drafting, the existing distribution action remains approval-gated and must pass the dedicated outreach policy/sender path before any external send. The current one-initial-message/no-autonomous-follow-up rule remains unchanged.

The composer still cannot invent personalization, prior relationships, traction, testimonials, audience size, performance, urgency or scarcity, and it cannot insert its own URL; Partizan adds the exact tracked destination only after experiment preparation.

### Growth planning and portfolio sequencing

`GrowthPlanningEngine` applies `marketing-ideas + customer-research + prospecting` inside the durable Distribution Growth Manager portfolio path.

The integration is deterministic and bounded. It does not create a second planner agent, does not make another LLM call and cannot execute a play. It contributes at most ±10 points to the portfolio score and records the component rationale for every recommended item.

The planning adjustment uses already-persisted runtime facts:

- Audience Intelligence confidence, repeated independent evidence and demand/commercial-intent signals;
- `time_to_signal_days` to prefer faster learning when evidence and economics are otherwise similar;
- `effort_hours` to prefer lower-cost operational tests early;
- estimated test cost versus the product's remaining budget;
- a small compounding-channel bonus for community, owned-organic and partner/outreach work.

A play whose `estimated_cost_min` exceeds the remaining product budget is not sequenced into the portfolio. The portfolio budget allocator also refuses to recommend a per-play budget below `estimated_cost_min`; an infeasible expensive play is skipped rather than preventing cheaper later plays from being considered.

Observed experiment economics remain separate and stronger. Existing winner/loser adjustments from CAC, paid users and spend continue to dominate planning methodology, and the Growth Manager's `SCALE / CONTINUE / MODIFY / STOP` decisions are unchanged.

The existing platform-diversification bonus remains in place after planning scores are calculated, so Partizan can balance fast experiments with compounding channels without collapsing the portfolio into one surface.

## Design rules

1. **Curate, do not dump prompts.** Load only the 1–3 skill packs relevant to the current reasoning task.
2. **Pinned behavior.** Any upstream methodology refresh is an explicit code change and review.
3. **Structured output remains authoritative.** Skill guidance does not create a parallel data model.
4. **Hypothesis is not evidence.** Marketing reasoning may propose a segment, channel or angle; only observed data can validate it.
5. **Queries are not evidence.** Search instructions describe what Partizan asked for; source content describes what Partizan observed.
6. **No execution authority.** Skill text cannot spend, send, publish, approve, or bypass an execution policy.
7. **Deterministic where possible.** Evidence qualification and portfolio sequencing should not require an LLM when transparent scoring is sufficient.
8. **Observed economics win.** Methodology can prioritize what to test next, but measured CAC, conversions, spend and revenue remain stronger than heuristic planning signals.
9. **Dogfood before breadth.** Future skills should be added because real acquisition evidence identifies a quality gap, not merely because another skill exists upstream.

## Updating the upstream adaptation

When intentionally refreshing from `coreyhaines31/marketingskills`:

1. review upstream changes rather than following the default branch automatically;
2. update `UPSTREAM_COMMIT` and only the curated Partizan principles that are still useful;
3. preserve the Partizan authority boundary;
4. update pinned skill versions where applicable;
5. add or update tests for routing and prompt composition;
6. keep deterministic scoring and provenance tests where applicable;
7. keep the third-party notice current.

See `THIRD_PARTY_NOTICES.md` for attribution and license text.
