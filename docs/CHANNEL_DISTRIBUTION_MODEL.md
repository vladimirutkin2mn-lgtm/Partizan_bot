# Channel-first distribution model

## Why this document exists

The product direction was clarified after the initial Partizan Bot milestones. The earlier architecture
was centered on `Product → ICP → Channel Hunter → Growth Play → Experiment`. That remains useful,
but it is too coarse for the intended product.

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

A second important clarification is:

> For community distribution, the client should not be required to risk or reconfigure a personal
> social account. Partizan may own and operate its own transparent distribution identities/accounts.

## User input

The user should describe the product and desired audience in natural language. Audience constraints
can include:

- language;
- geography;
- demographic / behavioural constraints when known;
- interests / use cases / pain points;
- current audience assumptions;
- acquisition goal;
- total budget;
- max CAC / CPA;
- allowed channels and brand constraints.

The user is still the source of truth for product facts. Partizan may research the external market,
but should not silently invent product claims.

## Three product engines

### 1. Audience Intelligence

Goal: determine where the requested audience is concentrated.

Output: an **Audience Distribution Map** across ecosystems such as Telegram, Instagram, Reddit,
TikTok, YouTube, Google Search, X, Discord, newsletters, niche sites and forums.

For each ecosystem, Partizan should estimate:

- audience relevance;
- reachable scale;
- intent strength;
- discoverability of concrete opportunities;
- expected acquisition economics;
- confidence in the estimate.

The output should not merely say that a platform is good. It should lead to concrete surfaces and
opportunities.

### 2. Distribution Engine

Goal: map every audience opportunity to realistic ways of gaining access to it.

The hierarchy should become:

```text
Platform
  → Surface
    → Opportunity
      → Tactic
        → Distribution Identity (where needed)
          → Experiment
```

Platform-specific opportunity granularity can differ. For example:

```text
Telegram Community:
DistributionOpportunity = channel/group
DistributionAction = comment / standalone post / reply

Instagram Community:
DistributionOpportunity = external creator/account
DistributionAction = comment under a fresh relevant Reel/Post
```

The system should prefer the **coarsest useful persistent opportunity unit** that supports learning. It
should not default to message/user-level intelligence when community/creator-level testing is sufficient.

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

The existing Execution, Analytics and Growth Manager milestones remain useful here.

## Four classes of distribution tactics

For each Platform/Surface/Opportunity, Partizan should consider four classes.

### A. Paid platform advertising

Official ad products operated by the platform or ad network, for example:

- Meta Ads;
- Google Ads;
- Telegram Ads;
- Reddit Ads;
- TikTok Ads;
- YouTube Ads.

Partizan should surface targeting mechanisms, test budget, current price estimates where available,
expected effect ranges, confidence, setup requirements and observed results after launch.

Pricing estimates must be ranges with provenance/confidence and should be replaced by observed data as
soon as an experiment runs.

### B. Direct paid distribution

Buy access directly from someone who already owns the audience, for example creator integrations,
newsletter sponsorships, sponsored posts, affiliate/rev-share deals and paid partnerships.

This is useful but may be excluded from a platform's MVP when negotiation/payment/measurement makes the
workflow too operationally complex.

### C. Owned organic distribution

Publish content through media the client already owns, where it is valuable and convenient to connect
those assets. Examples include brand Instagram posts, TikTok videos, YouTube Shorts, owned Telegram
channels, SEO/content pages and landing pages.

This remains a supported tactic class, but it is **not** the required account model for community
acquisition. A client should not have to expose a personal Telegram/Reddit/Instagram account to use
Partizan community distribution.

### D. Community / guerrilla distribution

This is a first-class product capability. It is defined as **relevant native participation around
communities/audiences**, not raw spam volume.

The MVP should avoid over-engineering perfect message-level intelligence when platform-level,
community-level or creator-level relevance is sufficient.

Typical flow:

```text
find a relevant community / creator / audience surface
  → select a suitable Partizan Distribution Identity
  → inspect only enough local context to avoid an irrelevant action
  → generate a native contribution
  → execute within platform constraints
  → route interest through an attributable profile/landing layer where appropriate
  → measure downstream conversion
```

Partizan may own and operate the accounts used for this layer. Those accounts should be transparent
operator/brand/community identities rather than disposable personas pretending to be unrelated users.

The product should not make fake-account farms, mass unsolicited spam, undisclosed impersonation or
technical ban-evasion its core infrastructure. The moat should come from identifying relevant audience
surfaces, durable distribution presence, learning which surfaces convert and measuring real acquired
users.

## Partizan-owned Distribution Network

### Why this is the preferred community account model

Requiring a client to connect a personal account creates poor UX and asymmetric risk:

- the client may fear account restrictions;
- the client may not want Partizan posting from a personal identity;
- the client may not want to change profile name, avatar, bio or destination;
- the client may not have an account on the target platform;
- onboarding becomes much harder.

Therefore community execution should be able to use a **Partizan-owned Distribution Network**.

### Distribution Identity

A `DistributionIdentity` is an account/profile controlled by Partizan and assigned to a theme,
platform and operating context.

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

Examples of public positioning:

- Partizan AI Scout;
- Partizan Relationship Scout;
- Partizan Crypto Scout;
- Partizan Startup Scout.

The exact branding can evolve. The important principle is that the account is genuinely operated by
Partizan and is not represented as an independent customer secretly endorsing a client.

### Identity selection

For a community opportunity, Partizan should choose an identity based on:

- topical fit;
- language;
- community/creator eligibility;
- recent activity and health;
- profile relevance;
- previous conversion performance;
- frequency/anti-spam guardrails;
- client conflicts / brand safety;
- campaign assignment.

Target flow:

```text
Opportunity
  → candidate identities
  → eligibility / health check
  → best-fit Distribution Identity
  → message/action generation
  → execution
  → attribution
```

### Network effect

Over time the network itself can become a defensible asset. Partizan learns:

- which communities/creators permit and reward useful participation;
- which identity themes fit which audience surfaces;
- what types of contributions produce profile/product interest;
- which surfaces produce activated and paid users;
- which communities/creators have poor economics or moderation friction.

This creates a distribution graph and historical learning layer that a generic LLM does not have.

## Profile funnel and intermediate landing layers

For community distribution, a direct product link inside every message/comment is not the desired
default.

A useful funnel can be:

```text
native community contribution
  → interest in the Distribution Identity
  → profile view
  → profile bio / pinned destination
  → routing / landing layer
  → client product / Telegram bot / app
```

The profile is therefore part of acquisition and should be treated as an optimisable conversion asset.
Partizan may test positioning, bio, pinned destination and routing while preserving transparent identity.

An intermediate landing/routing layer can legitimately provide:

- explanation before deep-linking into a bot/app;
- UTM/referral attribution;
- source-specific positioning;
- A/B testing;
- analytics;
- consistent routing across platforms.

It must be treated as a conversion/measurement layer, not as cloaking designed to evade moderation.

# Telegram MVP — clarified product model

Canonical detailed note: `docs/TELEGRAM_MVP.md`.

Telegram has two independent acquisition engines:

```text
Telegram Paid
  +
Telegram Community
```

### Telegram Community core model

```text
DistributionOpportunity = channel/group
DistributionAction = comment / standalone post / reply
DistributionIdentity = Partizan-owned operator account
Experiment = bounded attributable test
```

The MVP is intentionally **community-level, not message-level**. Partizan finds relevant channels with
comments and public groups, then uses lightweight fresh context for individual actions.

It should not build deep NLP over every message or per-user purchase-intent scoring. The primary
optimisation target is which communities produce starts, activations and paid users.

# Instagram MVP — clarified product model

Canonical notes:

- `docs/INSTAGRAM_MVP.md` — account/media strategy;
- `docs/INSTAGRAM_COMMUNITY_MVP.md` — creator surfaces, actions, campaign slots and attribution.

Instagram separates four modes:

```text
Instagram Paid              → client ad account
Instagram Community         → Partizan-owned Distribution Identities
Client-Owned Organic        → optional client Professional account
Partizan Media Network      → Post-MVP
```

### Instagram Community core model

```text
DistributionOpportunity = external creator/account
DistributionAction = comment under a fresh relevant Reel/Post
DistributionIdentity = Partizan-owned thematic Instagram account
CampaignSlot = one active client campaign on an identity for a bounded test window
Experiment = creator/action batch measured primarily at campaign level
```

The persistent opportunity is the **creator/account**, not an individual commenter or individual
message. A fresh Reel/Post is only an action surface.

The MVP should:

- discover relevant creators/accounts;
- score them at creator/account level;
- choose a fresh thematically suitable Reel/Post;
- inspect only enough context to keep the comment relevant;
- select a suitable Partizan Distribution Identity;
- generate/execute a native comment;
- route interest through the Partizan profile funnel;
- measure campaign/batch-level downstream conversion.

Because the default external comment does not contain a unique direct product link, the MVP should not
pretend to have exact comment-level attribution. It should measure the bounded identity/client campaign
slot, store creator/action timestamps, and use creator-level correlations only as supporting evidence
unless stronger attribution becomes available.

The MVP should **not** build deep comment/user purchase-intent analysis, cold-DM acquisition, creator
negotiation, paid creator integrations or perfect comment-level attribution.

### Instagram Identity Maintenance vs Media Network

Partizan may publish limited evergreen/native content on Distribution Identities to keep profiles
coherent and useful. This is supporting infrastructure, not a follower-growth KPI.

A true Partizan Media Network that intentionally grows large thematic audiences is Post-MVP and should
only be built after Partizan observes repeated vertical demand and strong Instagram economics.

## New desired user-facing output

Instead of returning only a list of Growth Plays, Partizan should show a channel portfolio.

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

Then Partizan recommends a bounded multi-channel test portfolio and Growth Manager reallocates budget
based on observed results.

## What changes in the current architecture

Existing building blocks remain valuable:

- ProductProfile;
- ICP Engine;
- Execution;
- Analytics Loop;
- Growth Manager;
- learning memory.

The biggest redesign is Channel Hunter / Distribution Engine.

Current concept:

```text
ICP → list of ChannelOpportunity URLs
```

Target concept:

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

New likely core entities include:

- `PlatformOpportunity` / `AudiencePlatformScore`;
- `Surface`;
- richer `DistributionOpportunity`;
- `DistributionAction`;
- `Tactic`;
- `DistributionIdentity`;
- `CampaignSlot` where identity-level attribution requires a bounded active client assignment;
- `IdentityEligibility` / account health;
- `ExperimentAttributionRoute`.

`GrowthPlay` should become an executable tactic hypothesis tied to a concrete opportunity rather than
being responsible for discovering what kinds of distribution exist.

## Next product-design task: channel matrix

For every platform answer the same questions:

1. **Audience discovery** — how do we decide this audience is there?
2. **Surfaces** — what concrete object do we search for?
3. **Opportunity data** — what metadata/evidence must we collect?
4. **Standard paid tactics** — official ad mechanisms and economics.
5. **Direct paid tactics** — creator/admin/newsletter/partner access.
6. **Owned organic tactics** — what owned assets can Partizan operate?
7. **Community/guerrilla tactics** — what native actions are possible?
8. **Distribution identity model** — client-owned, Partizan-owned, ambassador/creator or none?
9. **Automation level** — full / approval-gated / assisted/manual.
10. **Required integration/account** — what infrastructure is required?
11. **Attribution** — how a user is tied back to the opportunity/experiment.
12. **Cost/effect model** — how Partizan estimates cost, reach, conversion and CAC.
13. **Risks/constraints** — operational and platform-specific limitations.

Initial platforms:

```text
Telegram
Instagram
Reddit
TikTok
YouTube
Google
X
Discord
Newsletters / niche sites
```

This matrix is the product-design prerequisite for the next major Channel Hunter / Distribution Engine
revision and should be completed before significant web UI work resumes.
