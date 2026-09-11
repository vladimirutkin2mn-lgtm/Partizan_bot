# Community Distribution Execution Plan

## Purpose

This is the canonical implementation and acceptance tracker for turning Telegram and Reddit from research-only channels into executable Partizan distribution channels.

GitHub master tracker: **#256 — Community Distribution programme — Telegram + Reddit**.

It complements:

- `docs/CHANNEL_DISTRIBUTION_MODEL.md` — product/domain model;
- `docs/TELEGRAM_MVP.md` — Telegram opportunity model;
- `docs/REDDIT_MVP.md` — Reddit opportunity and policy model;
- `docs/TELEGRAM_RESEARCH_CONNECTOR.md` — Phase 2 implementation and production verification contract;
- `docs/TELEGRAM_CLIENT_OWNED_PUBLISH.md` — Phase 3 client-owned publish contract;
- `docs/TELEGRAM_CLIENT_OWNED_GOVERNANCE.md` — Phase 3 observation/automation governance;
- `docs/REDDIT_RESEARCH_COMMUNITY_POLICY.md` — Phase 4 research/freshness/policy contract.

When implementation and older docs disagree about the execution path, this document defines the current rollout until the underlying canonical docs are updated.

## Customer promise

The customer journey is:

```text
Product
  → Research
  → Distribution opportunities
  → Choose channel
  → Choose who publishes
  → Connect only what that choice needs
  → Execute
  → Measure
  → Learn / repeat
```

Publisher modes are independent from research capability:

1. `MANUAL` — Partizan finds the opportunity and drafts the contribution; the customer publishes it.
2. `CLIENT_OWNED` — the customer connects an account; Partizan can publish through that authorised account only when platform/community/readiness/approval gates permit it.
3. `PARTIZAN_MANAGED` — premium execution through eligible Partizan-managed or partner-managed publisher inventory. Disposable fake-account farms, impersonation, vote manipulation, spam and ban-evasion infrastructure are out of scope.

## Cross-platform capability model

Every channel is decomposed into:

```text
SEARCH → DRAFT → PUBLISH → MEASURE
```

A channel must never be presented as executable merely because credentials/configuration exist. Capability readiness is explicit and fail-closed.

## Persistent opportunity and local target model

```text
Telegram Community
  persistent opportunity = channel/group
  local target = recent public message/thread surface where required

Reddit Community
  persistent opportunity = subreddit
  local target = fresh relevant public thread for COMMENT/REPLY
```

The persistent unit supports longitudinal learning. Local message/thread context is supporting execution evidence, not a replacement opportunity identity.

## Telegram target flow

```text
Product + ICP
  → discover public channels/groups
  → community relevance
  → inspect bounded recent context
  → MANUAL | CLIENT_OWNED | PARTIZAN_MANAGED
  → policy/readiness/approval gates
  → publish where permitted
  → observe delivery/removal/restriction signals
  → attribute downstream outcomes
```

Telegram research credentials and client-owned publishing credentials/readiness remain separate.

## Reddit target flow

```text
Product + ICP
  → discover candidate subreddits
  → research/refresh CommunityPolicy
  → hard freshness + policy gate
  → rank relevance/activity/policy/prior outcomes
  → choose subreddit + action type
  → require a fresh verified thread target for comment/reply
  → draft
  → MANUAL | CLIENT_OWNED | PARTIZAN_MANAGED
  → publish only through a separately ready execution path
  → measure outcomes
```

`CommunityPolicy` is a hard execution gate, not a ranking hint. Reddit research does not itself enable Reddit publishing credentials or `PUBLISH` readiness.

## Pricing / packaging model

Keep the customer-facing money concepts separate:

```text
Research fee
Execution / management fee
Distribution spend
```

Partizan internal operating cost is tracked separately for economics and pricing learning. Measured economics must be driven by observed evidence; estimates and synthetic fixtures do not qualify as real acceptance evidence.

## Current production / acceptance state

The latest Community Distribution acceptance tooling is merged in PR #269 as release `7a6e57ce7bcf06cd2ccfbbf5968976fec110e796`.

- Main CI #888 / run `34577723485` succeeded on the exact merge SHA.
- Production deploy #629 / run `34577863942` built the exact release, applied migrations, started API/workers, passed worker health, returned 200 from internal `/health/live` and `/health/ready`, and internal `/version` returned the exact release SHA.
- The deploy remains red only at the public HTTPS handshake because incident #266 is still present at the shared Caddy TLS/SNI boundary. The same TLS failure reproduces against loopback SNI and no certificate is served for `partizanlabs.com`; Partizan application containers are healthy.
- PR #269 adds a read-only production acceptance report for #250–#255 and a manual `Community distribution acceptance` workflow. The report inspects persisted production evidence inside the API container over SSH and does not depend on the public HTTPS edge.
- The report does not invoke research or publishing providers, mutate readiness, create synthetic evidence, or change issue state. It can only summarize readiness and durable evidence that already exists.

Issue #266 is an infrastructure incident, not proof that any channel acceptance gate is complete. Likewise, a green CI/deploy-internal smoke does not replace the real-production evidence explicitly required by #250–#255.

## GitHub execution tracker

| Phase | Issue | Current status |
|---|---|---|
| 1. Channel execution foundation | #249 | **Complete** — PR #248 merged and production verified |
| 2. Telegram research connector | #250 | PR #257 merged/deployed; code/test acceptance complete; **9/10**; real production research evidence still required and production research credentials/readiness are not configured |
| 3. Telegram client-owned publish | #251 | PRs #260 and #261 merged/deployed; code/governance acceptance complete; **9/10**; real production publish + result/observation still required |
| 4. Reddit research + CommunityPolicy | #252 | PR #262 merged/deployed; **9/10**; bounded production research against real public subreddits still required |
| 5. Reddit client-owned publish | #253 | PR #264 merged/deployed; **6/10**; production OAuth/commercial/API readiness, downstream attribution evidence, and real publish/outcome verification remain |
| 6. Partizan Managed Distribution | #254 | PR #265 merged; **9/10**; at least one real managed distribution experiment with measurable outcome remains |
| 7. Economics + learning loop | #255 | PR #268 merged; **7/8**; at least one real end-to-end customer experiment with observed costs/outcomes and a measured next decision remains |

The read-only acceptance evidence runner is PR #269. It is an audit mechanism for these gates, not an eighth implementation phase and not a substitute for real evidence.

Issue checklists are authoritative acceptance criteria. Issue #256 stays open until every phase that requires real evidence has actually satisfied it.

## Delivery phases

### Phase 1 — Channel execution foundation

PR #248 is merged and production verified.

- [x] Typed publisher modes.
- [x] Typed `SEARCH / DRAFT / PUBLISH / MEASURE` capabilities.
- [x] Explicit fail-closed readiness metadata.
- [x] Publisher-mode choice independent from research preference.
- [x] Independent auth, billing, spend and provider gates.

Issue #249 is closed.

### Phase 2 — Telegram research connector

PR #257 is merged/deployed. `docs/TELEGRAM_RESEARCH_CONNECTOR.md` holds the detailed contract.

- [x] Authorised bounded read-only Telethon research transport exists in the implementation.
- [x] Public channel/group discovery from ProductProfile + ICP.
- [x] Community persistent unit; recent messages are context/evidence only.
- [x] Stable entity-ID dedupe and source/freshness provenance.
- [x] Fresh action target only when recent observed context supports it.
- [x] No participant enumeration, joining, invitations or mass unsolicited messaging.
- [x] Research credentials separated from publisher mode and `PUBLISH` readiness.
- [x] Integration/security tests.
- [x] Fail-closed production verification confirms missing provider/session configuration does not leak secrets or enable execution.
- [ ] Real production research run on an exact deployed release.

The final item remains open. Production previously verified `TELEGRAM_RESEARCH_PROVIDER=unavailable`, `TELEGRAM_RESEARCH_PUBLIC_READY=false`, with no configured API ID/hash/authorised research session. Before #250 can close, an authorised research-only session must be provisioned and a bounded real public-community result persisted with source/entity/freshness evidence.

### Phase 3 — Telegram client-owned publish

PR #260 added fail-closed customer-owned Telegram session/publishing. PR #261 added post-publish observation and explicit per-project automation authorization. Both are merged and deployed.

- [x] Secure customer account connection/session lifecycle.
- [x] Draft comment/reply/standalone contribution from local context.
- [x] Approval-gated first publish path.
- [x] Publish result/failure/restriction audit receipt.
- [x] Read-only post-publish presence/removal/restriction observation.
- [x] Per-project automation authorization with explicit customer approval, pause/revoke and readiness re-checks.
- [x] Frequency/duplicate/daily guardrails and no mass-DM/join/invite path.
- [x] Session secrets stay server-side/encrypted and are excluded from browser/log receipts.
- [x] Research and client-owned publishing readiness remain independent and fail-closed.
- [ ] Real production client-owned publish + result/observation verification.

Issue #251 stays open until the final real publish/result evidence exists.

### Phase 4 — Reddit research + CommunityPolicy

PR #262 is merged/deployed. Detailed behavior lives in `docs/REDDIT_RESEARCH_COMMUNITY_POLICY.md`.

- [x] Discover candidate subreddits from ProductProfile + ICP.
- [x] Keep subreddit as persistent opportunity and thread as local ActionTarget.
- [x] Research/store policy evidence with source, research status and freshness.
- [x] Model commercial participation, links/mentions, standalone posts, comments/replies and special constraints.
- [x] Treat missing/stale/ambiguous policy as a hard execution blocker.
- [x] Accept comment/reply ActionTargets only when thread freshness is verifiable and bounded.
- [x] Rank opportunities using relevance, fresh activity, policy fit and prior measured outcomes.
- [x] Keep Reddit research independent from Reddit publishing credentials/readiness.
- [x] Tests cover provider failure, stale policy, disallowed actions and fail-closed behavior.
- [ ] Bounded real-production research against real public subreddits.

Before #252 closes, production evidence must show concrete subreddit opportunities, policy provenance/freshness, fresh thread provenance where available, ambiguous rules staying fail-closed, and no publishing/OAuth capability becoming enabled as a side effect.

### Phase 5 — Reddit client-owned publish

PR #264 is merged/deployed and keeps publishing readiness independent from research readiness.

- [ ] Production-appropriate Reddit OAuth/account connection exists.
- [ ] Required Reddit commercial/API permissions are explicitly verified before execution is enabled.
- [x] Customer tokens/secrets are encrypted and remain server-side.
- [x] APPROVED-only standalone/comment/reply publish adapter exists where policy permits.
- [x] Approval queue and explicit per-publish customer confirmation exist.
- [x] Rate limits, cooldowns, duplicate-content and daily guardrails exist.
- [x] No voting, karma farming, unsolicited DM/join automation, moderation or ban-evasion path.
- [x] Observable score/reply/removal signals attach to the exact DistributionAction.
- [ ] Real downstream attribution evidence where permitted.
- [ ] Real production publish + outcome polling verification.

Production remains fail-closed until Reddit commercial/API access and OAuth configuration are genuinely ready. Research success alone must never enable publishing.

### Phase 6 — Partizan Managed Distribution

PR #265 is merged. The implementation provides explicit inventory ownership, eligibility/health/capacity gates, deterministic selection, campaign-slot conflict reservation, cost separation, internal publisher audit, and customer-safe managed-service views.

- [x] Publisher inventory registry.
- [x] Explicit Partizan-managed vs partner-managed ownership.
- [x] Eligibility, health, topic/language fit, allowed actions/surfaces and capacity.
- [x] Campaign assignment and client-conflict guardrails.
- [x] Fit/activity/outcome/capacity-aware selection.
- [x] Distribution spend, operational cost and management fee recorded separately.
- [x] Customer sees a managed service outcome rather than hidden account mechanics.
- [x] No disposable fake-account farm, impersonation, vote manipulation, spam or ban evasion.
- [x] Internal audit identifies the managed/partner publisher used for each action.
- [ ] At least one real managed distribution experiment with measurable outcome.

`MANAGED_DISTRIBUTION_PUBLIC_READY=false` remains the fail-closed default. Issue #254 cannot close from inventory/configuration alone.

### Phase 7 — Economics + learning loop

PR #268 is merged. It separates observed research/execution/distribution/internal operating costs, compares publisher modes/action types/opportunities, reuses observed replies/removals in durable learning, derives pricing assumptions only from observed operating-cost samples, and keeps internal operating cost out of customer-facing economics.

- [x] Normalize research fee, execution/management fee, distribution spend and internal operating cost separately.
- [x] Ingest/persist replies, removals, visits, activations, paid users and revenue where available.
- [x] Compare economics across `MANUAL`, `CLIENT_OWNED` and `PARTIZAN_MANAGED`.
- [x] Feed observed outcomes into `STOP / CONTINUE / MODIFY / SCALE` decisions.
- [x] Persist channel/community/action-type learning for future research.
- [x] Update pricing assumptions from observed operating cost rather than hard-coded guesses.
- [x] Keep customer-facing reporting separated from Partizan internal operating cost.
- [ ] At least one real end-to-end customer experiment produces a measured next decision from observed cost/outcome evidence.

Only explicit `OBSERVED` spend/evidence may satisfy the real economics gate. Estimate/synthetic evidence remains useful for testing but cannot close #255.

## Production acceptance evidence workflow

PR #269 adds `.github/workflows/community-distribution-acceptance.yml` and `tools/report_community_distribution_acceptance.py`.

Use the manual **Community distribution acceptance** workflow after meaningful production execution/research evidence changes. It can optionally be scoped to a customer project UUID.

The report must remain read-only:

- no provider calls;
- no publishing or research execution;
- no readiness mutation;
- no credential/secret disclosure;
- no issue closure;
- no synthetic evidence creation.

For a phase to report production verification, the report requires a production environment, durable database-backed runtime state, a concrete release SHA, and the exact same-action/same-experiment evidence chain required by that phase.

The workflow runs over SSH inside the existing production API container, so public TLS incident #266 does not prevent evidence inspection. The incident still must be repaired separately before public HTTPS can be considered healthy.

## Definition of done for the programme

The programme is done only when a real customer can:

1. add/confirm a product;
2. receive Telegram and Reddit opportunities;
3. choose channel and publisher mode;
4. complete only the setup required for that choice;
5. execute at least one real policy-compliant action;
6. see execution status and measurable outcome;
7. receive a next decision based on observed data.

Configured credentials, UI buttons, mocked connectors, green unit tests and synthetic events do not by themselves close a phase or the programme.

## Tracking discipline

Each phase has a dedicated GitHub issue. PRs reference the issue they advance and update this document when sequencing, scope or acceptance state changes.

A phase closes only after its issue acceptance criteria are satisfied, including real production evidence where explicitly required. Operational blockers must remain visible rather than being converted into code-complete claims.
