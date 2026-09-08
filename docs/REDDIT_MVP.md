# Reddit MVP — community, policy and paid distribution model

> **Execution model:** `docs/COMMUNITY_DISTRIBUTION_EXECUTION_PLAN.md` is the canonical rollout tracker for Reddit execution. The persistent opportunity is still the subreddit, `CommunityPolicy` is still a hard gate, and publishing is selected independently through `MANUAL`, `CLIENT_OWNED`, or `PARTIZAN_MANAGED` publisher mode.

## Decision summary

Reddit fits the Partizan channel-first model especially well because the persistent audience unit is usually a **subreddit/community**.

The MVP should treat Reddit as two independent but connected acquisition engines:

```text
Reddit Community
  +
Reddit Paid
```

The central community model is:

```text
DistributionOpportunity = subreddit
DistributionAction = standalone post OR comment/reply
PublisherMode = MANUAL | CLIENT_OWNED | PARTIZAN_MANAGED
PublisherIdentity = explicit executor when applicable
ActionTarget = subreddit OR a fresh relevant thread
Experiment = bounded set of actions measured against downstream acquisition
```

Partizan should optimise **which subreddits produce starts, activations and paid users**, not attempt to identify the perfect individual Reddit user or infer purchase intent for every comment.

A subreddit is only commercially usable when its rules allow the intended action. Therefore Reddit introduces a mandatory first-class object: **CommunityPolicy**.

## Why the subreddit is the opportunity unit

The MVP should use the coarsest persistent unit that supports learning.

For Reddit, this is usually the subreddit because it captures:

- audience concentration;
- topic/vertical;
- moderation rules;
- posting and comment eligibility;
- community-specific history;
- previous Partizan performance;
- paid targeting adjacency.

A specific thread is usually an **action surface**, not the persistent acquisition opportunity.

Example:

```text
Opportunity
r/relationships

Actions
  → standalone post in r/relationships
  → reply under fresh thread A
  → reply under fresh thread B

Learning
  → starts / activations / paid / removals / restrictions
```

The system should not default to message-level or user-level intelligence when subreddit-level testing is sufficient.

## Audience discovery

Given a ProductProfile + ICP, Partizan should generate Reddit discovery queries and identify relevant subreddits.

The discovery objective is:

```text
ICP
  → candidate subreddits
  → subreddit metadata
  → CommunityPolicy
  → relevance / activity / policy fit
  → selected Reddit Opportunities
```

## DistributionOpportunity — subreddit

Illustrative fields:

```text
id
platform = reddit
subreddit_name
url
title
description
language
topic / vertical
member_count / size estimate
activity estimate
freshness
posting eligibility
comment eligibility
community_policy_id
audience_relevance_score
previous experiment count
previous removals/restrictions
attributed visits
activations
paid users
performance summary
status
```

The most important output is not vanity audience size but whether the subreddit is relevant, usable and economically productive.

## CommunityPolicy — mandatory Reddit entity

Reddit communities can have materially different rules. A highly relevant subreddit may still be unusable for a commercial experiment.

Partizan should record a `CommunityPolicy` for every candidate subreddit before generating an executable promotional/community action.

Illustrative fields:

```text
subreddit_id
rules_source / evidence
last_checked_at
commercial_participation_allowed
self_promotion_allowed
links_allowed
product_mentions_allowed
standalone_posts_allowed
comments_allowed
disclosure_required
frequency_constraints
special_promotion_windows
ai_content_constraints
confidence
notes
```

The exact schema can evolve, but the product rule should remain:

> Policy fit is a **gate**, not merely another weak ranking feature.

Examples:

```text
promotion_allowed = false
  → no commercial Reddit Community experiment

standalone_posts_allowed = false
comments_allowed = true
  → comments/replies only

links_allowed = true
  → direct attributable link may be used when the contribution itself is allowed
```

Partizan should not use profile routing or publisher identity selection as a way to circumvent a subreddit rule that prohibits promotion.

## Publisher modes

Publisher mode answers **who performs the final community publish action**. It is independent from Reddit Paid assets and from the research capability itself.

### `MANUAL`

Partizan discovers the subreddit/thread, checks policy and drafts the contribution. The customer performs the final publish action. This is the lowest-operational-cost path and requires no Partizan Reddit publishing credentials.

### `CLIENT_OWNED`

The customer explicitly connects an authorised Reddit account. Partizan may publish through it only after account connection, platform/commercial API readiness, CommunityPolicy, approval and rate-limit gates are all satisfied.

`CLIENT_OWNED` must remain fail-closed until Partizan has the production-appropriate Reddit API/commercial permissions needed for the intended customer execution.

### `PARTIZAN_MANAGED`

Partizan handles distribution through eligible Partizan-managed or partner-managed publisher inventory. This is a premium service. The internal identity registry must record ownership, topic/language fit, eligibility/health, allowed surfaces/actions, capacity and campaign assignment.

Managed distribution must not make disposable account farms, impersonation, vote manipulation, karma farming, mass unsolicited engagement or technical ban evasion part of the architecture.

## Reddit Community actions

The MVP has two primary action types.

### 1. Standalone post

Used when subreddit policy permits posting and the generated contribution fits the community.

Flow:

```text
selected subreddit
  → CommunityPolicy gate
  → choose publisher mode / eligible identity
  → generate useful/native standalone post
  → optional transparent product mention/link only when permitted
  → approval / handoff where required
  → publish through supported execution path
  → measure downstream outcome
```

The post should provide standalone value. The product mention should not be disguised as an unrelated independent customer endorsement.

### 2. Comment / reply

Used around a fresh relevant thread.

The MVP should use lightweight local context only:

```text
selected subreddit
  → CommunityPolicy gate
  → fresh relevant thread
  → read enough of the thread to stay relevant
  → generate useful reply
  → approval / handoff where required
```

Do not build deep analysis of every commenter, exhaustive conversation graphs or per-user purchase-intent scoring for MVP.

## ActionTarget — fresh relevant thread

For comment/reply actions, a thread is an execution surface.

Candidate thread filters can remain simple:

- subreddit already approved as an Opportunity;
- reasonably fresh;
- topic related to the client campaign;
- comments are open;
- action permitted by CommunityPolicy;
- enough context available to avoid an irrelevant response.

The thread itself does not need a complicated standalone opportunity score.

## Reddit Community attribution

### When direct product links are permitted

Use an attributable route such as:

```text
DistributionAction
  → campaign-specific routing URL
  → client product
  → activation
  → paid
```

This can produce action-level or near-action-level attribution.

### When direct links are not permitted but commercial participation is still allowed

Use campaign/profile-level attribution only when consistent with community rules. Do not claim perfect action-level attribution in this mode.

For managed publishers, the publisher profile may be part of the route. For client-owned publishers, the customer's authorised profile/destination applies. For manual mode, Partizan records the handoff and any attributable route the customer elects to use.

## Reddit Community experiment model

A bounded experiment should aggregate several actions in a small set of approved subreddits.

Example:

```text
Product — Reddit relationships experiment

Opportunities
  → 8 approved subreddits

Actions
  → 5 standalone posts
  → 16 comments/replies

Results
  → visits
  → activations
  → paid users
  → removals / restrictions
  → revenue

Decision
  → STOP / CONTINUE / MODIFY / SCALE
```

The learning unit is primarily the **subreddit + action type + publisher mode/identity + campaign**, not an inferred individual-user intent score.

## Reddit Paid Engine

Reddit Ads should remain separate from community execution.

The valuable product loop is:

```text
Audience discovery
  → relevant subreddit/community clusters
  → Reddit Community experiments
  +
  → Reddit Ads targeting around the same audience clusters where supported
  → compare CAC / CPA / ROAS
```

Partizan should manage paid tests through the client's Reddit advertising/business assets when required.

Paid capabilities to model include:

- community targeting;
- keyword targeting;
- interest targeting;
- geography;
- custom audiences where available;
- creative variants;
- campaign/ad group/ad setup;
- spend;
- conversion tracking;
- CAC / CPA / ROAS.

The exact current Reddit Ads/API capabilities and commercial-access requirements must be refreshed from current official sources at execution time rather than hard-coded permanently into product logic.

## Paid attribution

For Reddit Ads, Partizan should support the platform's currently available conversion measurement stack where authorised.

Target funnel:

```text
impression
  → click
  → signup/start
  → activation
  → paid
  → revenue
```

Paid and Community outcomes should be reported separately before Growth Manager compares them.

## Execution architecture

Reddit Community execution must not assume a universal unrestricted publishing API across all discovered communities.

Model the system as:

```text
Discovery Engine
  → CommunityPolicy parser/checker
  → Opportunity selection
  → ActionTarget selection where needed
  → Draft capability
  → PublisherMode / PublisherIdentity selection
  → Execution Adapter or manual handoff
  → Outcome Adapter
  → Analytics / learning
```

Execution can begin approval-gated or manual where authorised integration capabilities do not support the required action cleanly.

The product should prefer supported/authorised execution and community eligibility over enforcement avoidance.

## MVP scope table

| Capability | MVP |
|---|---|
| Subreddit discovery | Yes |
| Subreddit-level Opportunity scoring | Yes |
| CommunityPolicy parsing/checking | Yes — mandatory |
| `MANUAL` publisher mode | Yes |
| `CLIENT_OWNED` publisher mode | Yes after required Reddit execution/API readiness |
| `PARTIZAN_MANAGED` publisher mode | Yes after managed inventory exists |
| Standalone posts where permitted | Yes through a ready publisher mode |
| Comments/replies where permitted | Yes through a ready publisher mode |
| Lightweight thread-context reading | Yes |
| Deep user/comment purchase-intent analysis | No |
| Client personal Reddit account required | No — optional publisher mode only |
| Direct product links | Only where community rules permit |
| Profile/campaign funnel | Secondary, only where consistent with rules |
| Reddit Ads | Yes as a separate paid engine |
| Cold private-message acquisition | No |
| Vote manipulation / karma farming | No |
| Disposable account farm | No |
| Ban-evasion infrastructure | No |

## Primary Reddit MVP learning questions

1. Which subreddits actually contain the requested ICP?
2. Which relevant subreddits permit useful commercial participation?
3. Which action type works better by subreddit: standalone post or comment/reply?
4. Which publisher mode and eligible identities perform best in which communities?
5. What CAC/CPA does Reddit Community produce?
6. What CAC/CPA does Reddit Ads produce against the same audience clusters where supported?
7. Which subreddit/action/publisher patterns should Growth Manager `STOP / CONTINUE / MODIFY / SCALE`?

## Canonical cross-platform simplification

The cross-platform opportunity units remain:

```text
Telegram Community → channel/group
Instagram Community → creator/account
Reddit Community → subreddit
```

In each case, Partizan optimises a persistent audience surface first and only then chooses a lightweight local execution target. **Who publishes is a separate execution choice, not part of the opportunity identity.**
