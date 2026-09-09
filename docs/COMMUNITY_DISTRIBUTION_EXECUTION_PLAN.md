# Community Distribution Execution Plan

## Purpose

This is the canonical implementation tracker for turning Telegram and Reddit from research-only channels into executable Partizan distribution channels.

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

Keep three money concepts separate:

```text
Research fee
Execution / management fee
Distribution spend
```

Pricing should be validated against real operating cost and acquisition outcomes before hard-coding plan amounts.

## GitHub execution tracker

| Phase | Issue | Current status |
|---|---|---|
| 1. Channel execution foundation | #249 | **Complete** — PR #248 merged and production verified |
| 2. Telegram research connector | #250 | PR #257 merged/deployed; code/test acceptance complete; final real production research evidence deferred until production Telegram research config is installed |
| 3. Telegram client-owned publish | #251 | PRs #260 and #261 merged/deployed; code-only acceptance complete; only real production client-owned publish + result/observation remains |
| 4. Reddit research + CommunityPolicy | #252 | **Implementation in draft PR #262**; production research evidence remains a final acceptance gate |
| 5. Reddit client-owned publish | #253 | Planned after #252 code/policy foundation; external API/commercial readiness is independent and fail-closed |
| 6. Partizan Managed Distribution | #254 | Planned; depends on foundation and at least one proven execution path |
| 7. Economics + learning loop | #255 | Planned; requires real execution evidence |

By explicit project direction, final Telegram production verification may be deferred while later code phases proceed. This does not waive or close #250/#251 and does not enable any fail-closed Telegram capability.

Issue checklists are the acceptance criteria. Issue #256 stays open until every phase closes from real evidence where required.

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

- [x] Authorised bounded read-only Telethon research transport.
- [x] Public channel/group discovery from ProductProfile + ICP.
- [x] Community persistent unit; recent messages are context/evidence only.
- [x] Stable entity-ID dedupe and source/freshness provenance.
- [x] Fresh action target only when recent observed context supports it.
- [x] No participant enumeration, joining, invitations or mass unsolicited messaging.
- [x] Research credentials separated from publisher mode and `PUBLISH` readiness.
- [x] Integration/security tests.
- [ ] Real production research run on an exact deployed release.

The final item is intentionally still open until production Telegram research credentials/readiness are installed and one bounded real run is captured.

### Phase 3 — Telegram client-owned publish

By explicit product direction, implementation proceeded while the final Phase 2 production evidence gate was deferred. That sequencing decision did not waive Phase 2 and production activation stayed fail-closed.

PR #260 added the fail-closed customer-owned Telegram session/publish path. PR #261 added post-publish observation and explicit per-project automation authorization. Both are merged and deployed.

- [x] Secure customer account connection/session lifecycle.
- [x] Draft comment/reply/standalone contribution from local context.
- [x] Approval-gated first publish path.
- [x] Publish result/failure/restriction audit receipt.
- [x] Read-only post-publish presence/removal/restriction observation.
- [x] Per-project automation authorization with explicit customer approval, pause/revoke and readiness re-checks.
- [x] Frequency/duplicate/daily guardrails and no mass-DM/join/invite path.
- [ ] Real production client-owned publish + result/observation verification.

Issue #251 stays open for the final production acceptance item.

### Phase 4 — Reddit research + CommunityPolicy

Implementation is in draft PR #262. Detailed behavior lives in `docs/REDDIT_RESEARCH_COMMUNITY_POLICY.md`.

Target code contract:

- discover candidate subreddits from ProductProfile + ICP;
- keep subreddit as persistent opportunity and thread as local ActionTarget;
- research/store policy evidence with source, research status and freshness;
- model commercial participation, self-promotion, links, product mentions, standalone posts, comments/replies, disclosure, designated promotion surfaces and AI-content constraints;
- treat missing/stale/ambiguous policy as a hard execution blocker;
- accept comment/reply ActionTargets only when thread freshness is verifiable and within the bounded freshness window;
- rank Reddit opportunities using relevance, fresh activity, policy fit and prior measured outcomes;
- keep Reddit research independent from Reddit publishing credentials/readiness;
- preserve fail-closed behavior under provider failure, stale policy and unverified thread timestamps.

Code/test checkboxes remain pending in issue #252 until #262 is merged. Issue #252 must remain open after merge until real production research against real subreddits is verified.

### Phase 5 — Reddit client-owned publish

- [ ] Production-appropriate Reddit OAuth/account connection.
- [ ] Confirm required API/commercial permissions before customer execution is enabled.
- [ ] Approval queue and publish adapter.
- [ ] Rate limits, cooldowns, duplicate-content guardrails and audit trail.
- [ ] Poll observable score/replies/removal signals and downstream outcomes.

Phase 4 research success alone must never enable Phase 5 publishing.

### Phase 6 — Partizan Managed Distribution

- [ ] Publisher inventory registry.
- [ ] Explicit Partizan-managed vs partner-managed ownership.
- [ ] Eligibility, health, topic/language fit and capacity.
- [ ] Campaign assignment and client-conflict guardrails.
- [ ] Cost accounting and management fee.
- [ ] No disposable fake-account farm, impersonation, vote manipulation or ban evasion.

### Phase 7 — Economics + learning loop

- [ ] Normalize action cost, management cost and distribution spend.
- [ ] Attribute visits/activations/paid/revenue where possible.
- [ ] Compare `MANUAL`, `CLIENT_OWNED` and `PARTIZAN_MANAGED` economics.
- [ ] Feed results into `STOP / CONTINUE / MODIFY / SCALE` decisions.
- [ ] Validate customer pricing from real execution data.

## Definition of done for the programme

The programme is done only when a real customer can:

1. add/confirm a product;
2. receive Telegram and Reddit opportunities;
3. choose channel and publisher mode;
4. complete only the setup required for that choice;
5. execute at least one real policy-compliant action;
6. see execution status and measurable outcome;
7. receive a next decision based on observed data.

Configured credentials, UI buttons, mocked connectors and synthetic events do not by themselves close a phase or the programme.

## Tracking discipline

Each phase has a dedicated GitHub issue. PRs reference the issue they advance and update this document when sequencing or scope changes.

A phase closes only after its issue acceptance criteria are satisfied, including real production evidence where the issue explicitly requires it.
