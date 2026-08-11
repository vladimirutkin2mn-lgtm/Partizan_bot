# TikTok MVP — content-cluster distribution model

## Decision summary

TikTok should not be modeled as a copy of Instagram.

The persistent learning unit for TikTok is a **content/topic cluster**, not a single creator, video, commenter or user.

Core model:

```text
DistributionOpportunity = content/topic cluster

Evidence / action surfaces =
  creators
  videos
  hashtags
  keywords
  recurring formats / hooks

Acquisition engines =
  TikTok Community
  + Partizan Organic Experiments
  + TikTok Paid
```

Example opportunity for a relationship product:

```text
ContentCluster = breakup / relationship advice

Evidence:
- relevant creators
- recent videos
- hashtags
- keywords
- recurring hooks/formats

Tactics:
- comments under relevant external videos
- Partizan-owned organic videos
- TikTok Ads / paid scaling
```

The product should learn which **topics/content clusters** produce real activations and paid users, then reuse that learning across community, organic and paid distribution.

## Why Content Cluster is the Opportunity

TikTok discovery and distribution are strongly content-driven. A creator is useful evidence and an execution target, but the more durable strategic object is the theme that repeatedly attracts the target audience.

For example:

```text
breakup advice
zodiac compatibility
AI productivity
small business bookkeeping
home workout mistakes
```

A creator can stop posting or change topic. A successful content cluster can be reused across many creators, video formats and paid creatives.

Therefore:

```text
ContentCluster
  → creators
  → videos
  → hashtags / keywords
  → formats / hooks
  → Community tactics
  → Organic tactics
  → Paid tactics
  → measured economics
```

The system should not default to per-user purchase-intent scoring or deep comment-level intelligence.

## Engine A — TikTok Community

Account ownership: **Partizan-owned Distribution Identity**.

### Opportunity

```text
DistributionOpportunity = ContentCluster
```

### Action targets

Within a cluster Partizan finds:

- relevant creators;
- fresh relevant videos;
- hashtags / keywords that help discover current surfaces.

A creator or video is normally an **ActionTarget / evidence source**, not the long-lived opportunity entity.

### Primary MVP action

```text
DistributionAction = comment under a fresh relevant external video
```

Partizan should inspect only enough local video/caption context to keep the comment relevant.

Do not build:

- deep analysis of every commenter;
- per-user purchase-intent models;
- cold-DM acquisition;
- mass generic comments;
- creator negotiation as part of Community MVP.

### Execution model

Do not assume the standard commercial TikTok developer stack provides a universal endpoint for Partizan to leave arbitrary comments on third-party videos.

Therefore MVP architecture should separate:

```text
Discovery
  → ContentCluster scoring
  → ActionTarget selection
  → Comment generation
  → assisted / operator execution adapter
  → analytics
```

The execution path can evolve later if a compliant automation route is proven.

## Engine B — Partizan Organic Experiments

This is the major difference from the Instagram MVP.

Partizan-owned TikTok identities may publish original short-form videos as **first-class acquisition experiments**, not merely as profile maintenance.

Example:

```text
ContentCluster = breakup advice
  → generate 10 video hypotheses
  → publish bounded test portfolio
  → TikTok organic distribution
  → profile / attributable destination
  → activation
  → paid
```

Each video is treated as an experiment that can teach Partizan:

- which topic angle works;
- which hook works;
- which creative format works;
- which CTA works;
- which cluster deserves more content;
- whether the same learning should be reused in Paid.

### Important distinction: Organic Experiment vs Media Asset

MVP objective:

> Produce attributable acquisition experiments and learn which content works.

MVP objective is **not**:

> Grow a Partizan TikTok account to a large follower base as a standalone media business.

Follower count is therefore not a primary MVP KPI.

Primary metrics are downstream acquisition outcomes such as:

```text
views / reach
  → profile / destination visits where measurable
  → activations
  → paid users
  → revenue
  → content production cost
  → effective CAC / CPA
```

## Post-MVP — Partizan TikTok Media Network

A true Media Asset intentionally grows a reusable owned audience and follower base.

That remains Post-MVP.

Decision rule:

```text
serve clients
  → identify repeated high-performing TikTok clusters / verticals
  → prove organic conversion economics
  → intentionally build dedicated Partizan media assets in those verticals
```

This avoids forcing MVP to prove both the acquisition engine and a full media-company model at the same time.

## Engine C — TikTok Paid

Account ownership: **client advertising/business assets when paid acquisition is enabled**.

TikTok Paid should be a first-class MVP acquisition engine.

Partizan should convert ContentCluster learning into paid hypotheses:

```text
ContentCluster
  → audience / targeting hypothesis
  → creative hypothesis
  → TikTok Ads test
  → spend
  → activation / paid
  → CAC / CPA / ROAS
```

The important product loop is that Community, Organic and Paid should learn from the same persistent content-cluster entity.

Example:

```text
breakup advice cluster

Community:
comments around relevant videos

Organic:
Partizan videos using tested hooks

Paid:
creative / targeting variants derived from the same cluster
```

Growth Manager then compares the actual economics of all three engines.

## Organic winner → Paid scaling

A strong future/secondary MVP mechanic is:

```text
Partizan organic video performs strongly
  → identify winning hook / angle / creative
  → reuse the learning in paid creative
  → where supported and authorised, scale eligible organic content via platform paid formats such as Spark Ads
```

The product value is not only finding a winning video. It is turning organic evidence into a paid scaling decision.

## Distribution Identities

TikTok Community and Partizan Organic Experiments use **Partizan-owned thematic Distribution Identities**.

Illustrative themes:

```text
AI & Tech
Relationships & Lifestyle
Business & Startups
Finance
Wellness
Entertainment
```

The client should not need to connect a personal TikTok account to use Partizan Community or Partizan Organic experiments.

These identities should be durable, coherently positioned Partizan-operated accounts rather than disposable personas or fake independent customers.

## Client-Owned Organic — optional

If the client explicitly connects and authorises a suitable brand/creator account, Partizan may prepare or help publish organic TikTok content on that account.

This mode is optional because:

- some clients have strict brand identity requirements;
- some do not want AI-generated short-form content in their feed;
- some do not want to delegate publishing;
- Partizan should still be able to test TikTok without requiring the client's social account.

## Publishing / integration principle

TikTok's official content-publishing capabilities and approval requirements are platform-dependent and can change.

Do **not** design MVP around an assumption that Partizan can silently and fully automate publishing across an internally managed account network through the standard Content Posting API.

MVP should instead separate:

```text
AI content generation
  → approval / scheduling layer
  → compliant publishing / assisted execution path
  → analytics
```

Current official API eligibility, audit requirements and account-control requirements should be refreshed at implementation/execution time.

## Discovery architecture

Do not assume the standard authenticated-user TikTok API is a universal search index for all public TikTok content.

Partizan needs a dedicated discovery capability that can produce:

```text
ContentCluster
  → relevant keywords
  → hashtags
  → creators
  → fresh videos
  → engagement / activity evidence
```

The implementation may use compliant public-search, browser, partner/data-provider or other approved discovery mechanisms depending on what is available at implementation time.

Research-only APIs should not be treated as the default commercial production dependency unless Partizan is explicitly eligible for them.

## Opportunity data model

Illustrative `TikTokContentCluster` fields:

```text
id
platform = TIKTOK
name / topic
language
geography hints
ICP relevance
keywords
hashtags
relevant_creator_count
recent_video_activity
representative_creators
representative_videos
recurring_hooks
recurring_formats
community_hypothesis_strength
organic_hypothesis_strength
paid_hypothesis_strength
historical_actions
historical_content_experiments
historical_paid_experiments
visits / activations / paid / revenue
estimated_cac
confidence
last_refreshed_at
```

The content cluster should accumulate learning across all engines instead of creating disconnected Community, Organic and Paid memories.

## Attribution

### Partizan Organic

Use an attributable profile / routing destination and bounded publishing windows where needed.

Where individual video-level outbound attribution is available, use it. Otherwise measure at identity/content experiment level without claiming false precision.

### TikTok Community

External comments will often not carry unique product links. Default attribution may therefore be identity/campaign/batch-level, similar to Instagram Community.

Store creator/video/action timestamps as supporting evidence for later learning.

### TikTok Paid

Use the strongest supported ad-platform attribution and server/client conversion instrumentation available at implementation time.

Paid should support direct CAC/CPA/ROAS comparison with Organic and Community.

## MVP scope table

| Capability | MVP |
|---|---|
| Content/topic-cluster discovery | Yes |
| Keyword / hashtag discovery | Yes |
| Creator/video discovery within a cluster | Yes |
| Partizan-owned TikTok Distribution Identities | Yes |
| Relevant external-video comments | Yes — assisted/operator execution initially |
| Partizan-owned organic TikTok video experiments | Yes |
| AI generation of video concepts/scripts/captions | Yes |
| Follower growth as primary KPI | No |
| Large Partizan TikTok Media Network | No — Post-MVP |
| TikTok Ads | Yes |
| Organic winner → paid creative reuse / Spark-style scaling where supported | Yes as scaling path |
| Client TikTok account required | No |
| Client-Owned Organic | Optional |
| Deep user/comment purchase-intent analysis | No |
| Cold-DM acquisition | No |
| Creator negotiation / direct paid integrations | Post-MVP |
| Mass generic commenting | No |
| Fully automatic managed-account publishing assumed through standard API | No |

## Primary TikTok MVP learning questions

1. Which content clusters contain the client's audience?
2. Which clusters convert through community participation?
3. Which clusters and hooks convert when Partizan publishes its own short-form content?
4. Which organic winners can improve paid creative performance?
5. How do Community, Organic and Paid CAC compare for the same cluster?
6. Which recurring verticals eventually justify a dedicated Partizan TikTok Media Asset?

## Cross-platform opportunity granularity

The current channel model becomes:

```text
Telegram  → Opportunity = channel/group
Instagram → Opportunity = external creator/account
Reddit    → Opportunity = subreddit
TikTok    → Opportunity = content/topic cluster
```

TikTok's defining product advantage is that one persistent opportunity can drive **Community + Organic + Paid** learning at the same time.
