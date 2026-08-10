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

Example:

```text
Platform: Telegram
Surface: public discussion group
Opportunity: a specific live discussion/message
Tactic A: Telegram Ads
Tactic B: community participation
Distribution Identity: a Partizan-owned thematic operator account
Experiment: one bounded, attributable test
```

The same concrete opportunity may support several acquisition tactics.

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
acquisition. A client should not have to expose a personal Telegram/Reddit account to use Partizan.

### D. Community / guerrilla distribution

This is a first-class product capability. It is defined as **high-context distribution into relevant
conversations**, not as raw spam volume.

The valuable automation is:

```text
find the right conversation
  → understand context
  → choose a relevant tactic
  → choose the right Partizan Distribution Identity
  → generate a useful native contribution
  → execute within platform constraints
  → measure downstream conversion
```

Partizan may own and operate the accounts used for this layer. Those accounts should be transparent
operator/brand/community identities rather than disposable personas pretending to be unrelated users.

The product should not make fake-account farms, mass unsolicited spam, undisclosed impersonation or
technical ban-evasion its core infrastructure. The moat should come from finding unusually relevant
opportunities, having durable distribution presence, learning which communities convert and measuring
real acquired users.

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
community memberships
allowed surfaces/actions
reputation/history metadata
recent activity
current client assignments
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
- community membership/eligibility;
- recent activity and health;
- profile relevance;
- previous conversion performance;
- frequency/anti-spam guardrails;
- client conflicts / brand safety.

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

- which communities permit and reward useful participation;
- which identity themes fit which communities;
- what types of contributions produce profile/product interest;
- which surfaces produce activated and paid users;
- which communities have poor economics or moderation friction.

This creates a distribution graph and historical learning layer that a generic LLM does not have.

## Profile funnel and intermediate landing layers

For community distribution, a direct product link inside every message is not the desired default.

A useful funnel can be:

```text
useful contribution
  → interest in the Distribution Identity
  → profile view
  → profile bio / pinned destination
  → landing page or owned routing layer
  → client product / Telegram bot
```

The profile is therefore part of acquisition and should be treated as an optimisable conversion asset.
Partizan may test positioning, bio, pinned destination and routing while preserving transparent identity.

An intermediate landing layer can legitimately provide:

- explanation before deep-linking into a bot/app;
- UTM/referral attribution;
- source-specific positioning;
- A/B testing;
- analytics;
- consistent routing across platforms.

It must be treated as a conversion/measurement layer, not as cloaking designed to evade moderation.

# Telegram MVP — clarified product model

Telegram is the first platform where the account model has been clarified in detail.

## What we learned from manual testing

Directly dropping a Telegram bot/channel link into unrelated comments or groups can quickly trigger
moderation, anti-spam systems or bans. Whether a specific action is blocked automatically or reported by
users/admins varies by community, so Partizan should not build its core Telegram strategy around raw
link dropping.

The desired MVP has two independent acquisition engines:

```text
Telegram Paid
  +
Telegram Community
```

They should be measured and learned separately.

## Telegram Paid Engine

Included in MVP:

- discover relevant public Telegram channels/audience clusters;
- determine which are usable for Telegram Ads targeting/inventory;
- generate compliant ad variants;
- create bounded tests;
- route traffic to an attributable Telegram destination/deep link;
- measure start/activation/paid events;
- calculate CAC and reallocate budget.

Direct negotiation with channel administrators is **not MVP** because it creates negotiation, payment,
fraud and measurement complexity before we know whether the channel is economically important.

## Telegram Community Engine

Included in MVP:

- discover relevant public groups;
- discover linked discussion groups/comments where accessible;
- find specific live messages/conversations with high product relevance;
- score each conversation as an acquisition opportunity;
- choose a suitable Partizan-owned Telegram Distribution Identity;
- generate a useful contextual reply/contribution;
- execute only within explicit operational limits;
- measure downstream product traffic/conversion where possible.

The unit of opportunity is often a **specific live conversation**, not simply a channel/group.

## Telegram Distribution Identities

The MVP should use Partizan-owned Telegram user accounts/operator accounts for community execution.
They are not ordinary Bot API bots: a BotFather bot is a different product primitive and cannot simply
behave like a normal user account across arbitrary communities.

The client does **not** need to:

- connect a personal Telegram account;
- risk a personal account;
- change personal name/avatar/bio;
- allow Partizan to post under the client's personal identity.

The operator account should have a coherent thematic profile and durable history. Profile configuration
becomes part of the community funnel.

## Telegram MVP scope

| Capability | MVP |
|---|---|
| Audience/channel discovery | Yes |
| Group/discussion discovery | Yes |
| Specific conversation discovery | Yes |
| Opportunity scoring | Yes |
| Telegram Ads | Yes |
| Ad attribution to bot/product | Yes |
| Partizan-owned operator accounts | Yes |
| Distribution Identity selection | Yes |
| AI-generated contextual contributions | Yes |
| Profile funnel | Yes |
| Product conversion attribution | Yes |
| Client personal Telegram account required | No |
| Client profile modification required | No |
| Negotiation with channel admins | No |
| Direct paid placements with admins | Post-MVP |
| Follower/subscriber boosting | No |
| Disposable fake-account farm | No |
| Mass link spam / ban-evasion infrastructure | No |

## What Partizan optimises in Telegram Community

The goal is not "how many messages can we post". The primary optimisation target is acquired users and
business economics.

Candidate funnel:

```text
qualified conversation opportunity
  → contribution published
  → profile/product interest
  → bot / landing visit
  → first meaningful interaction
  → activation
  → paid
```

Useful metrics include:

- qualified opportunities found;
- contribution approval/eligibility rate;
- removals/restrictions as a negative signal;
- attributed product visits / bot starts;
- activation rate;
- paid users;
- CAC / cost per activated user;
- revenue / ROAS where spend exists;
- performance by community, message type and Distribution Identity.

Partizan should learn whether a specific community/identity/message pattern produces real users rather
than optimise vanity follower counts.

## Platform-specific examples to explore next

### Instagram

Still open for design. We must explicitly decide whether community distribution should also use
Partizan-owned distribution identities, connected brand accounts, creators/ambassadors or a mixture.
Paid ads and owned publishing are separate tactic classes.

### Reddit

Still open for design. The important opportunity unit is often a specific live thread/comment chain.
The account model should be evaluated separately rather than copied blindly from Telegram.

### Google

The opportunity unit is usually a search query / intent cluster rather than a social identity. Likely
tactics include Search Ads, landing pages, long-tail SEO, comparison pages and tools.

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
- richer `ChannelOpportunity` or renamed `DistributionOpportunity`;
- `Tactic`;
- `DistributionIdentity`;
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
7. **Community/guerrilla tactics** — what high-context native actions are possible?
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
