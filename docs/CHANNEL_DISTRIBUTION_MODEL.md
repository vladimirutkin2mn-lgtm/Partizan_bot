# Channel-first distribution model

## Why this document exists

The product direction was clarified after the initial Partizan Bot milestones. The earlier architecture was centered on `Product → ICP → Channel Hunter → Growth Play → Experiment`. That remains useful, but it is too coarse for the intended product.

The stronger product model is:

```text
Product + desired audience
  → understand where this audience actually spends time
  → find concrete places / conversations / inventory where the audience can be reached
  → enumerate viable acquisition tactics for every place
  → choose the execution identity / account where relevant
  → estimate price, expected effect and confidence
  → launch approved tactics
  → measure real outcomes
  → reallocate budget and generate the next test
```

The core distinction is:

> Partizan must answer **WHERE the audience is** before it answers **HOW to market to it**.

For community distribution, the client should not be required to risk or reconfigure a personal social account. Partizan may own and operate its own transparent distribution identities/accounts.

## MVP channel scope

Canonical scope note: `docs/MVP_CHANNEL_SCOPE.md`.

Partizan MVP is intentionally limited to four ecosystems:

```text
Telegram
Instagram
Reddit
TikTok
```

The following are **Post-MVP** and must not block implementation or launch:

```text
YouTube
Google Search
X
Discord
Newsletters
Niche sites
Forums
Any additional distribution source not in the four MVP ecosystems
```

The purpose of the MVP is to prove the full acquisition-learning loop, not maximum platform coverage.

## User input

The user should describe the product and desired audience in natural language. Audience constraints can include:

- language;
- geography;
- demographic / behavioural constraints when known;
- interests / use cases / pain points;
- current audience assumptions;
- acquisition goal;
- total budget;
- max CAC / CPA;
- allowed channels and brand constraints.

The user is still the source of truth for product facts. Partizan may research the external market, but should not silently invent product claims.

## Three product engines

### 1. Audience Intelligence

Goal: determine where the requested audience is concentrated.

For MVP, output an **Audience Distribution Map** across Telegram, Instagram, Reddit and TikTok.

For each ecosystem, Partizan should estimate:

- audience relevance;
- reachable scale;
- intent strength;
- discoverability of concrete opportunities;
- expected acquisition economics;
- confidence in the estimate.

The output should lead to concrete surfaces and opportunities.

### 2. Distribution Engine

Goal: map every audience opportunity to realistic ways of gaining access to it.

```text
Platform
  → Surface
    → Opportunity
      → Tactic
        → Distribution Identity (where needed)
          → Experiment
```

Platform-specific opportunity granularity differs:

```text
Telegram Community:
DistributionOpportunity = channel/group
DistributionAction = comment / standalone post / reply

Instagram Community:
DistributionOpportunity = external creator/account
DistributionAction = comment under a fresh relevant Reel/Post

Reddit Community:
DistributionOpportunity = subreddit
DistributionAction = standalone post / comment / reply

TikTok:
DistributionOpportunity = content/topic cluster
Evidence / ActionTargets = creators / videos / hashtags / keywords / formats
```

The system should prefer the **coarsest useful persistent opportunity unit** that supports learning. It should not default to message/user-level intelligence when community/creator/subreddit/topic-cluster-level testing is sufficient.

### 3. Growth Operator

Goal: execute, measure and learn.

Responsibilities:

- recommend the best test portfolio;
- prepare creatives / copy / targeting;
- choose the appropriate distribution identity where relevant;
- require appropriate approval before external actions;
- launch through supported providers/accounts;
- ingest visits, signups, paid users, revenue and spend;
- calculate CAC / CPA / ROAS;
- decide `SCALE / CONTINUE / MODIFY / STOP`;
- update learning memory and the next hypothesis.

## Four classes of distribution tactics

### A. Paid platform advertising

Official ad products operated by the platform or ad network, including Telegram Ads, Meta Ads, Reddit Ads and TikTok Ads for the MVP.

Partizan should surface targeting mechanisms, test budget, current price estimates where available, expected effect ranges, confidence, setup requirements and observed results after launch.

Pricing estimates must be ranges with provenance/confidence and should be replaced by observed data as soon as an experiment runs.

### B. Direct paid distribution

Buy access directly from someone who already owns the audience, for example creator integrations, sponsored posts, affiliate/rev-share deals and paid partnerships.

This is useful but may be excluded from a platform's MVP when negotiation/payment/measurement makes the workflow too operationally complex.

### C. Owned organic distribution

Publish content through media the client already owns where useful. Client-owned organic is optional and should not be a prerequisite for community acquisition.

TikTok additionally includes **Partizan-owned organic videos as first-class MVP acquisition experiments**. Instagram Partizan-owned content remains primarily Identity Maintenance in MVP.

### D. Community / guerrilla distribution

Community distribution means relevant native participation around communities/audiences, not raw spam volume.

Typical flow:

```text
find relevant audience surface
  → select a suitable Partizan Distribution Identity
  → inspect only enough local context to avoid an irrelevant action
  → generate a native contribution
  → execute within platform constraints
  → route interest through an attributable profile/landing layer where appropriate
  → measure downstream conversion
```

Partizan-owned accounts should be transparent operator/brand/community identities rather than disposable personas pretending to be unrelated users.

The product should not make fake-account farms, mass unsolicited spam, undisclosed impersonation or technical ban-evasion its core infrastructure.

## Partizan-owned Distribution Network

A `DistributionIdentity` is an account/profile controlled by Partizan and assigned to a theme, platform and operating context.

Illustrative fields:

```text
id
platform
theme / vertical
language
geography hints
public positioning
profile configuration
account health / eligibility
community memberships / creator history
allowed surfaces/actions
reputation/history metadata
recent activity
current campaign assignment
attribution route
status
```

Identity selection should consider topical fit, language, eligibility, activity/health, profile relevance, prior conversion performance, frequency guardrails, client conflicts and campaign assignment.

Over time the network becomes a distribution graph: Partizan learns which communities/creators/topics permit and reward useful participation and which actually produce activated and paid users.

## Profile funnel and intermediate routing

A direct product link inside every community message/comment is not the desired default.

```text
native community contribution
  → interest in Distribution Identity
  → profile view
  → bio / pinned destination
  → routing / landing layer
  → client product / bot / app
```

Routing may provide attribution, source-specific positioning, A/B testing, analytics and consistent deep-linking. It is a conversion/measurement layer, not moderation cloaking.

# Telegram MVP

Canonical note: `docs/TELEGRAM_MVP.md`.

Telegram has two engines:

```text
Telegram Paid
Telegram Community
```

Community model:

```text
DistributionOpportunity = channel/group
DistributionAction = comment / standalone post / reply
DistributionIdentity = Partizan-owned operator account
Experiment = bounded attributable test
```

The MVP is community-level, not message-level. Primary optimisation target: which communities produce starts, activations and paid users.

# Instagram MVP

Canonical notes:

- `docs/INSTAGRAM_MVP.md`;
- `docs/INSTAGRAM_COMMUNITY_MVP.md`.

```text
Instagram Paid              → client ad account
Instagram Community         → Partizan-owned Distribution Identities
Client-Owned Organic        → optional
Partizan Media Network      → Post-MVP
```

Community model:

```text
DistributionOpportunity = external creator/account
DistributionAction = comment under a fresh relevant Reel/Post
DistributionIdentity = Partizan-owned thematic Instagram account
CampaignSlot = one active client campaign on an identity for a bounded test window
Experiment = creator/action batch measured primarily at campaign level
```

Identity Maintenance is MVP supporting infrastructure. Growing large owned Instagram audiences is Post-MVP.

# Reddit MVP

Canonical note: `docs/REDDIT_MVP.md`.

Reddit has two engines:

```text
Reddit Community
Reddit Paid
```

Community model:

```text
DistributionOpportunity = subreddit
DistributionAction = standalone post / comment / reply
DistributionIdentity = Partizan-owned thematic Reddit account
ActionTarget = subreddit OR fresh relevant thread
Experiment = bounded subreddit/action batch
```

`CommunityPolicy` is mandatory before commercial execution. Direct links and commercial actions are used only where subreddit rules permit them.

# TikTok MVP

Canonical note: `docs/TIKTOK_MVP.md`.

TikTok has three first-class MVP engines:

```text
TikTok Community
Partizan Organic Experiments
TikTok Paid
```

Core model:

```text
DistributionOpportunity = content/topic cluster
Evidence / ActionTargets = creators / videos / hashtags / keywords / formats
DistributionIdentity = Partizan-owned thematic TikTok account
Experiment = bounded community / organic / paid test tied to the same cluster
```

Creators/videos are evidence/action surfaces inside a persistent topic cluster. Partizan-owned organic videos are acquisition experiments; follower growth and a true TikTok Media Network are Post-MVP.

## User-facing output

Instead of returning only Growth Plays, Partizan should show a channel portfolio across the four MVP ecosystems.

For each platform:

```text
opportunity score
  → concrete surfaces/opportunities found
  → available tactics
  → estimated price / test budget
  → expected effect range
  → confidence
  → automation level
  → execution identity/integration requirements
```

Then Partizan recommends a bounded multi-channel test portfolio and Growth Manager reallocates budget based on observed results.

## Architecture consequence

Existing building blocks remain valuable:

- ProductProfile;
- ICP Engine;
- Execution;
- Analytics Loop;
- Growth Manager;
- learning memory.

The biggest redesign is Channel Hunter / Distribution Engine.

```text
ICP
  → Audience Distribution Map
  → Platform
  → Surface
  → concrete Opportunity
  → tactic catalog
  → Distribution Identity selection where relevant
  → price/effect/confidence/automation metadata
  → Growth Play / Experiment
```

Likely core entities include:

- `PlatformOpportunity` / `AudiencePlatformScore`;
- `Surface`;
- richer `DistributionOpportunity`;
- `DistributionAction`;
- `Tactic`;
- `DistributionIdentity`;
- `CommunityPolicy` for rule-governed surfaces such as Reddit;
- `CampaignSlot` where bounded identity assignment is required;
- `IdentityEligibility` / account health;
- `ExperimentAttributionRoute`.

`GrowthPlay` should become an executable tactic hypothesis tied to a concrete opportunity rather than being responsible for discovering what kinds of distribution exist.

## MVP platform design status

```text
[x] Telegram
[x] Instagram
[x] Reddit
[x] TikTok
```

Post-MVP:

```text
YouTube
Google Search
X
Discord
Newsletters
Niche sites
Forums
Other platforms
```

The MVP channel-design phase is complete. The next major step is implementation/redesign of Audience Intelligence / Channel Hunter / Distribution Engine for the four MVP ecosystems before significant web UI work resumes.
