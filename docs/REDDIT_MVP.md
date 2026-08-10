# Reddit MVP — community, policy and paid distribution model

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
DistributionIdentity = Partizan-owned thematic Reddit account
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

Example for a relationship / astrology product:

```text
relationships
breakups
relationship advice
tarot
astrology
zodiac compatibility
dating advice
```

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

Partizan should record a `CommunityPolicy` for every candidate subreddit before generating a promotional/community action.

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

Partizan should not use profile routing as a way to circumvent a subreddit rule that clearly prohibits promotion.

## Partizan-owned Reddit Distribution Identities

The client should not need to connect a personal Reddit account for community distribution.

Partizan can operate durable thematic Reddit identities such as broad vertical/operator accounts.

Illustrative themes:

- AI & Tech;
- Relationships & Lifestyle;
- Business & Startups;
- Finance & Crypto;
- Gaming;
- Wellness.

The account is infrastructure for distribution and learning, not a disposable persona.

Identity selection should consider:

- topical fit;
- language;
- subreddit eligibility;
- account history/health;
- recent activity;
- previous community performance;
- current client/campaign assignment;
- brand safety and conflicts.

The product should not make disposable account farms, impersonation, vote manipulation, karma farming, mass unsolicited engagement or technical ban-evasion part of its architecture.

## Reddit Community actions

The MVP has two primary action types.

### 1. Standalone post

Used when the subreddit rules permit posting and the generated contribution fits the community.

Flow:

```text
selected subreddit
  → policy gate
  → choose Partizan Distribution Identity
  → generate useful/native standalone post
  → optional transparent product mention/link only when permitted
  → publish through supported execution path
  → measure downstream outcome
```

The post should provide standalone value. The product mention should not be disguised as an unrelated independent customer endorsement.

### 2. Comment / reply

Used around a fresh relevant thread.

The MVP should use lightweight local context only:

```text
selected subreddit
  → fresh relevant thread
  → read enough of the thread to stay relevant
  → generate useful reply
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

Reddit can support stronger community attribution than Instagram when subreddit rules permit a direct attributable link.

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

Use campaign/profile-level attribution only when consistent with the community rules:

```text
useful contribution
  → Partizan identity/profile
  → stable routing layer
  → client product
```

Do not claim perfect action-level attribution in this mode.

## Reddit Community experiment model

A bounded experiment should aggregate several actions in a small set of approved subreddits.

Example:

```text
Oracle — Reddit relationships experiment

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

The learning unit is primarily the **subreddit + action type + identity + campaign**, not an inferred individual-user intent score.

## Reddit Paid Engine

Reddit Ads should be a first-class MVP channel and remain separate from community execution.

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

The exact current Reddit Ads capabilities/pricing must be refreshed from current official sources at execution time rather than hard-coded permanently into product logic.

## Paid attribution

For Reddit Ads, Partizan should support the platform's available conversion measurement stack, such as browser-side and server-side conversion instrumentation where available.

Target funnel:

```text
impression
  → click
  → signup/start
  → activation
  → paid
  → revenue
```

Paid and Community outcomes should be reported separately before the Growth Manager compares them.

## Organic-to-paid scaling — secondary experiment

A useful later experiment is:

```text
organic Reddit contribution performs well
  → eligible post/creative becomes a paid hypothesis
  → test with Reddit Ads
```

This is secondary to the core MVP. Do not make it a prerequisite for initial Reddit support.

## Execution architecture

Reddit Community execution should not assume a universal unrestricted publishing API across all discovered communities.

Model the system as:

```text
Discovery Engine
  → CommunityPolicy parser/checker
  → Opportunity selection
  → Distribution Identity selection
  → Content generation
  → Execution Adapter
  → Analytics
```

Execution can begin approval-gated or operator-assisted where official integration capabilities do not support the required action cleanly.

The product should prefer supported/authorised execution and community eligibility over building around enforcement avoidance.

## MVP scope table

| Capability | MVP |
|---|---|
| Subreddit discovery | Yes |
| Subreddit-level Opportunity scoring | Yes |
| CommunityPolicy parsing/checking | Yes — mandatory |
| Partizan-owned Reddit Distribution Identities | Yes |
| Standalone posts where permitted | Yes |
| Comments/replies where permitted | Yes |
| Lightweight thread-context reading | Yes |
| Deep user/comment purchase-intent analysis | No |
| Client personal Reddit account required | No |
| Direct product links | Only where community rules permit |
| Profile/campaign funnel | Secondary, only where consistent with rules |
| Reddit Ads | Yes |
| Paid community/keyword/interest targeting | Yes where currently supported |
| Paid conversion attribution | Yes |
| Cold private-message acquisition | No |
| Moderator negotiation/outreach | Post-MVP |
| Paid moderator/community deals | Post-MVP |
| Vote manipulation / karma farming | No |
| Disposable account farm | No |
| Ban-evasion infrastructure | No |
| Own Partizan subreddits | Post-MVP |

## Primary Reddit MVP learning questions

1. Which subreddits actually contain the requested ICP?
2. Which relevant subreddits permit useful commercial participation?
3. Which action type works better by subreddit: standalone post or comment/reply?
4. Which Partizan Reddit Distribution Identities perform best in which communities?
5. What CAC/CPA does Reddit Community produce?
6. What CAC/CPA does Reddit Ads produce against the same audience clusters?
7. Which subreddit/action patterns should Growth Manager `STOP / CONTINUE / MODIFY / SCALE`?

## Canonical cross-platform simplification

The cross-platform opportunity units are now:

```text
Telegram Community → channel/group
Instagram Community → creator/account
Reddit Community → subreddit
```

In each case, Partizan optimises a persistent audience surface first and only then chooses a lightweight local execution target.
