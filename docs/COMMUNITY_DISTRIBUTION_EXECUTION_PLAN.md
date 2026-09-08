# Community Distribution Execution Plan

## Purpose

This is the canonical implementation tracker for turning Telegram and Reddit from research-only channels into executable Partizan distribution channels.

GitHub master tracker: **#256 — Community Distribution programme — Telegram + Reddit**.

It complements:

- `docs/CHANNEL_DISTRIBUTION_MODEL.md` — product/domain model;
- `docs/TELEGRAM_MVP.md` — Telegram opportunity model;
- `docs/REDDIT_MVP.md` — Reddit opportunity and policy model;
- `docs/TELEGRAM_RESEARCH_CONNECTOR.md` — Phase 2 implementation and production verification contract.

When implementation and older docs disagree about the execution path, this document defines the current planned rollout until the underlying canonical docs are updated.

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

For Telegram and Reddit, the customer chooses one publishing mode:

1. `MANUAL` — Partizan finds the opportunity and drafts the contribution; the customer publishes it.
2. `CLIENT_OWNED` — the customer connects an account; Partizan can publish through that authorised account, subject to platform/community rules and approval settings.
3. `PARTIZAN_MANAGED` — Partizan handles distribution through eligible Partizan-managed or partner-managed publisher inventory. This is a premium execution service and must not depend on disposable fake-account farms, impersonation, vote manipulation, spam, or ban-evasion infrastructure.

These modes are independent from the channel capability itself.

## Cross-platform capability model

Every channel is decomposed into four capabilities:

```text
SEARCH → DRAFT → PUBLISH → MEASURE
```

- `SEARCH`: discover and score concrete audience surfaces/opportunities.
- `DRAFT`: generate a channel-native action using enough local context and policy/rule evidence.
- `PUBLISH`: execute the approved action through the selected publisher mode.
- `MEASURE`: ingest delivery/removal/reply signals plus downstream visits, activation, paid users and revenue where attributable.

A channel must never be presented as executable merely because credentials/configuration exist. Capability readiness is explicit and fail-closed.

## Core entities

The implementation should converge on these concepts:

```text
ChannelCapability
PublisherMode
PublisherIdentity
DistributionOpportunity
CommunityPolicy
DistributionAction
ActionTarget
Experiment
ExperimentAttributionRoute
```

`PublisherIdentity` ownership is explicit:

```text
CLIENT_OWNED
PARTIZAN_MANAGED
PARTNER_MANAGED
```

The customer-facing `PARTIZAN_MANAGED` mode may use Partizan-managed or partner-managed inventory internally.

## Telegram target flow

```text
Product + ICP
  → discover public channels/groups
  → community-level relevance scoring
  → choose community
  → inspect enough recent context to draft a relevant action
  → MANUAL | CLIENT_OWNED | PARTIZAN_MANAGED
  → publish comment / reply / standalone contribution where permitted
  → collect publish/removal/reply signals
  → attribute downstream conversion
  → learn community economics
```

Telegram research is community-first. Message-level context may be used as supporting evidence, but is not the persistent opportunity unit.

Reference implementations may inform adapters, but third-party projects are not copied wholesale. Useful references currently include `DimaPhil/telegram_forwarder` for Telethon user-session transport patterns and `A1exZabr/tgtrigger` for monitoring/filtering/lead-scoring patterns.

## Reddit target flow

```text
Product + ICP
  → discover candidate subreddits
  → fetch/refresh CommunityPolicy
  → policy gate
  → choose subreddit + action type
  → inspect a fresh thread when reply/comment is selected
  → draft
  → MANUAL | CLIENT_OWNED | PARTIZAN_MANAGED
  → publish only where policy and platform access permit
  → collect score/replies/removal signals
  → attribute downstream conversion
  → learn subreddit/action economics
```

`CommunityPolicy` is a hard gate, not a ranking hint.

`meet447/Reddit-Copilot` is the strongest current reference for intent-thread discovery, PRAW/OAuth, draft/review queues and outcome polling. Partizan should reuse ideas, not import its application architecture wholesale.

Commercial Reddit execution must remain fail-closed until the required Reddit API/commercial permissions are actually available to Partizan.

## Pricing / packaging model

Keep three money concepts separate:

```text
Research fee
Execution / management fee
Distribution spend
```

Expected customer packaging:

- `MANUAL`: lowest execution cost; customer performs the final publish action.
- `CLIENT_OWNED`: higher than manual; Partizan operates the authorised client account and provides automation/measurement.
- `PARTIZAN_MANAGED`: highest; Partizan supplies and manages eligible distribution inventory and operational execution.

Pricing should be validated against real operating cost and acquisition outcomes before hard-coding plan amounts.

## GitHub execution tracker

| Phase | Issue | Current status |
|---|---|---|
| 1. Channel execution foundation | #249 | **Complete** — PR #248 merged; foundation verified in production |
| 2. Telegram research connector | #250 | PR #257 merged and deployed on `20aa9433dc7301cf541359535eb22e386a265f7e`; code/test acceptance complete; final real production research run blocked on missing production Telegram research credentials |
| 3. Telegram client-owned publish | #251 | Planned; must not start until #250 is complete |
| 4. Reddit research + CommunityPolicy | #252 | Planned; depends on #249 |
| 5. Reddit client-owned publish | #253 | Planned; depends on #249 and #252; external API/commercial readiness gate |
| 6. Partizan Managed Distribution | #254 | Planned; depends on #249 and at least one working publish path |
| 7. Economics + learning loop | #255 | Planned; requires real execution evidence |

The issue checklists are the acceptance criteria. This file defines sequencing and product intent; issues define what must be proven before a phase can close. Issue #256 is the one-page programme index and stays open until every phase closes.

## Delivery phases

### Phase 1 — Channel execution foundation

PR #248 is merged. The foundation is included in production release `20aa9433dc7301cf541359535eb22e386a265f7e`, and production deployment verification completed successfully.

- [x] Add typed publisher modes.
- [x] Add typed `SEARCH / DRAFT / PUBLISH / MEASURE` channel capabilities.
- [x] Expose capability/readiness metadata without claiming unsupported execution.
- [x] Persist publisher-mode choice independently from existing channel research/auto preference.
- [x] Preserve fail-closed auth, billing, spend and provider readiness gates.
- [x] Add tests and update canonical docs.

Phase issue #249 is closed as complete.

### Phase 2 — Telegram research connector

PR #257 is merged and deployed. Detailed evidence mapping lives in `docs/TELEGRAM_RESEARCH_CONNECTOR.md`.

Code and CI acceptance are complete. A live production verification attempt from temporary draft PR #258 / CI #808 reached the production API container on exact release `20aa9433dc7301cf541359535eb22e386a265f7e` and confirmed the remaining blocker is operational configuration: Telegram research is disabled, provider is `unavailable`, and no API ID/hash/authorised session is configured in production. The same live check confirmed Telegram `PUBLISH` and `CLIENT_OWNED` publishing remain fail-closed.

- [x] Authorised Telegram/Telethon session transport for read-only research with explicit operational readiness.
- [x] Discover public channels/groups relevant to ProductProfile + ICP, including native discovery even when generic web search has no hit.
- [x] Keep the community as the persistent opportunity; use recent messages only as supporting context/evidence.
- [x] Persist public source URL, Telegram entity ID, source-check time, observed activity freshness and surface capability metadata.
- [x] Deduplicate channel/group guesses by stable Telegram entity ID.
- [x] Surface a specific action-target URL only when observed recent context supports it and is within the freshness gate; otherwise preserve a community-level destination honestly.
- [x] Enforce bounded query/result/context limits and exclude participant enumeration, joining, invitations and mass unsolicited messaging.
- [x] Keep research credentials completely separate from publisher mode and `PUBLISH` readiness.
- [x] Integration tests cover native-only discovery, web/native dedupe, freshness provenance, bounded limits, native-provider failure fallback, no-secret leakage and fail-closed execution readiness.
- [ ] Verify at least one real production research run on an exact deployed release before closing #250.

To complete the last item, production must provide:

```text
TELEGRAM_RESEARCH_PROVIDER=telethon
TELEGRAM_RESEARCH_PUBLIC_READY=true
TELEGRAM_RESEARCH_API_ID=...
TELEGRAM_RESEARCH_API_HASH=...
TELEGRAM_RESEARCH_SESSION=...
```

Then rerun a bounded production research request and capture non-secret evidence for a real public community: source URL, Telegram entity ID, `source_checked_at`, freshness/context metadata, no secret leakage, and Telegram `PUBLISH` still fail-closed.

### Phase 3 — Telegram client-owned publish

Do not begin this phase until #250 is complete from real production research evidence.

- [ ] Secure customer account connection/session lifecycle.
- [ ] Draft comment/reply/standalone contribution from local context.
- [ ] Approval-gated first publish.
- [ ] Publish result, failure/removal/restriction signals and audit trail.
- [ ] Per-project automation can only be enabled after explicit customer approval and readiness checks.

### Phase 4 — Reddit research + CommunityPolicy

- [ ] Discover subreddit candidates.
- [ ] Parse/store/refresh subreddit policy evidence.
- [ ] Hard-gate disallowed commercial actions.
- [ ] Discover fresh thread action targets when comment/reply is selected.
- [ ] Rank opportunities using relevance, activity, policy fit and prior outcomes.

### Phase 5 — Reddit client-owned publish

- [ ] Production-appropriate Reddit OAuth/account connection.
- [ ] Confirm Partizan's required commercial/API permissions before enabling customer execution.
- [ ] Approval queue and publish adapter.
- [ ] Rate limits, cooldowns, duplicate-content guardrails and audit trail.
- [ ] Poll score/replies/removals and downstream outcomes.

### Phase 6 — Partizan Managed Distribution

- [ ] Publisher inventory registry.
- [ ] Explicit ownership: Partizan-managed vs partner-managed.
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

This programme is done only when a real customer can:

1. add/confirm a product;
2. receive Telegram and Reddit distribution opportunities;
3. choose a channel;
4. choose `MANUAL`, `CLIENT_OWNED` or `PARTIZAN_MANAGED`;
5. complete only the setup required for that choice;
6. execute at least one real, policy-compliant action;
7. see its execution status and measurable outcome;
8. receive a next decision based on observed data.

A mocked connector, configured credential, UI button, or synthetic event does not by itself satisfy these criteria.

## Tracking discipline

Each phase has a dedicated GitHub issue with acceptance criteria. PRs must reference the issue they advance and update this document when scope or sequencing changes.

A phase checkbox is marked complete only after its acceptance criteria are merged and, where applicable, production deployment/readiness is verified.
