# Marketing Intelligence

Partizan's Marketing Intelligence layer improves the quality of marketing reasoning without changing the execution control plane.

The first version is a curated, version-pinned adaptation of selected methodology from [`coreyhaines31/marketingskills`](https://github.com/coreyhaines31/marketingskills), pinned to commit `7868cb9251fad80a73d26e488a5ad5f6c4a9f335` and used under the upstream MIT license.

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

Marketing Intelligence is deliberately narrower. It supplies task-specific methodology and quality checks to LLM reasoning steps so that Partizan makes better marketing judgments before its existing schemas, policies and execution boundaries take over.

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
| `marketing-ideas` | 2.0.0 | Future growth-play selection and sequencing |
| `cold-email` | 2.0.0 | Future bounded creator/partner outreach drafting |
| `ad-creative` | 2.8.0 | Paid creative angles and grounded claims |

The registry contains the whole initial set, but registration does not imply that every skill is already invoked in production.

## Runtime integrations in the first release

### Product Intake

`ProductIntakeAgent` receives `product-marketing + customer-research` guidance.

The existing founder-source-of-truth rule remains authoritative. Marketing Intelligence encourages better extraction of the customer job, problem, desired outcome, alternatives and differentiation, but it cannot turn assumptions into product facts.

### ICP generation

`ICPEngine` receives `product-marketing + customer-research + prospecting` guidance.

The prompt now explicitly distinguishes an ICP hypothesis from observed customer evidence. When no external customer evidence is available, the model must not describe demand, willingness to pay, market size or personas as validated facts. ICP scores remain prioritization hypotheses for experiments.

### Distribution action drafting

`DistributionActionComposer` selects guidance by the already-authorized action type:

- `PAID_CAMPAIGN` -> `ad-creative + customer-research + product-marketing`;
- `ORGANIC_VIDEO` -> `influencer-marketing + ad-creative + customer-research`;
- `COMMENT`, `REPLY`, `STANDALONE_POST` -> `community-marketing + customer-research`.

The pre-existing Partizan action-drafting rules remain first: no impersonation, no fabricated experience or results, no generic spam, no cold DM through this surface, and no community promotion beyond the applied policy.

## Registered but not yet runtime-wired

The registry also defines task bundles for:

- `AUDIENCE_DISCOVERY`;
- `GROWTH_PLANNING`;
- `OUTREACH`.

These are intentional extension points, not claims of completed integration. They should be wired only to the corresponding durable Partizan modules, with tests and existing control-plane boundaries preserved.

In particular, `cold-email` must connect only to the bounded outreach path that already enforces contact provenance, suppression, delegation, send limits and reconciliation. It must not be routed through community action drafting.

## Design rules

1. **Curate, do not dump prompts.** Load only the 1–3 skill packs relevant to the current reasoning task.
2. **Pinned behavior.** Any upstream methodology refresh is an explicit code change and review.
3. **Structured output remains authoritative.** Skill guidance does not create a parallel data model.
4. **Hypothesis is not evidence.** Marketing reasoning may propose a segment, channel or angle; only observed data can validate it.
5. **No execution authority.** Skill text cannot spend, send, publish, approve, or bypass an execution policy.
6. **Dogfood before breadth.** Future skills and channel-specific methodology should be added because real acquisition evidence identifies a quality gap, not merely because another skill exists upstream.

## Updating the upstream adaptation

When intentionally refreshing from `coreyhaines31/marketingskills`:

1. review upstream changes rather than following the default branch automatically;
2. update `UPSTREAM_COMMIT` and only the curated Partizan principles that are still useful;
3. preserve the Partizan authority boundary;
4. update pinned skill versions where applicable;
5. add or update tests for routing and prompt composition;
6. keep the third-party notice current.

See `THIRD_PARTY_NOTICES.md` for attribution and license text.
