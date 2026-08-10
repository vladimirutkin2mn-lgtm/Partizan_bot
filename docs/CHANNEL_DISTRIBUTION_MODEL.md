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
  → enumerate all viable acquisition tactics for every place
  → estimate price, expected effect and confidence
  → launch approved tactics
  → measure real outcomes
  → reallocate budget and generate the next test
```

The core distinction is:

> Partizan must answer **WHERE the audience is** before it answers **HOW to market to it**.

## User input

The user should describe the product and desired audience in natural language. Audience constraints
can include:

- language (for example English-speaking or Russian-speaking);
- geography;
- demographic / behavioural constraints when known;
- interests / use cases / pain points;
- current audience assumptions;
- acquisition goal;
- total budget;
- max CAC / CPA;
- allowed accounts / channels / brand constraints.

The user is still the source of truth for product facts. Partizan may research the external market,
but should not silently invent product claims.

## Three product engines

### 1. Audience Intelligence

Goal: determine where the requested audience is concentrated.

Output: an **Audience Distribution Map** across ecosystems such as:

- Telegram;
- Instagram;
- Reddit;
- TikTok;
- YouTube;
- Google Search;
- X;
- Discord;
- newsletters / niche sites / forums;
- other relevant ecosystems.

For each ecosystem, Partizan should estimate:

- audience relevance;
- reachable scale;
- intent strength;
- discoverability of concrete opportunities;
- expected acquisition economics;
- confidence in the estimate.

The output should not be merely "Instagram is good". It should lead to concrete surfaces and
opportunities.

### 2. Distribution Engine

Goal: map every audience opportunity to all realistic ways of gaining access to it.

The hierarchy should become:

```text
Platform
  → Surface
    → Opportunity
      → Tactic
        → Experiment
```

Example:

```text
Platform: Telegram
Surface: public discussion groups
Opportunity: a specific group/channel/comment thread
Tactic A: Telegram Ads
Tactic B: direct paid placement with the owner
Tactic C: partnership / affiliate agreement
Tactic D: founder/community participation from an authorised account
Tactic E: useful content created specifically for the community
```

The same concrete opportunity may therefore support several completely different acquisition
tactics.

### 3. Growth Operator

Goal: execute, measure and learn.

Responsibilities:

- recommend the best test portfolio;
- prepare creatives / copy / targeting / outreach;
- require appropriate human approval before external actions;
- launch through connected providers/accounts where supported;
- ingest visits, signups, paid users, revenue and spend;
- calculate CAC / CPA / ROAS;
- decide `SCALE / CONTINUE / MODIFY / STOP`;
- update learning memory and the next hypothesis.

The existing Execution, Analytics and Growth Manager milestones remain useful here.

## Four classes of distribution tactics

Partizan should not reduce marketing to "ads versus guerrilla". For each Platform/Surface/Opportunity,
it should consider four classes.

### A. Paid platform advertising

Official ad products operated by the platform or ad network, for example:

- Meta Ads;
- Google Ads;
- Telegram Ads;
- Reddit Ads;
- TikTok Ads;
- YouTube Ads.

Partizan should surface:

- available targeting mechanisms;
- minimum / recommended test budget;
- current price estimates where available;
- expected impressions/clicks/conversions as ranges;
- confidence level;
- setup requirements;
- actual results after launch.

Pricing estimates must be ranges with provenance/confidence. They should be replaced by observed data
as soon as an experiment runs.

### B. Direct paid distribution

Buy access directly from someone who already owns the audience:

- Telegram channel placement;
- creator integration;
- newsletter sponsorship;
- sponsored post;
- affiliate / rev-share deal;
- paid partnership.

Partizan should discover concrete inventory and, where possible, contact information / media kit / price.
It can then estimate expected economics and compare direct placement against platform ads.

### C. Owned organic distribution

Use the user's own connected accounts and owned media:

- Instagram Reels / posts / Stories;
- TikTok videos;
- YouTube Shorts;
- founder posts;
- Telegram channel content;
- Reddit posts from the user's account where appropriate;
- X posts/threads;
- SEO/content pages;
- landing pages and lead magnets.

The intended UX is that the client authorises relevant accounts. Partizan generates content and, where
stable official APIs permit it, can publish after approval.

### D. Community / guerrilla distribution

This is a first-class product capability, but it should be defined as **high-context distribution into
relevant conversations**, not as spam volume.

Examples:

- find a thread where someone explicitly discusses the problem the product solves;
- draft a useful founder response;
- discover a Telegram discussion where the target audience is active;
- identify a high-intent Reddit conversation;
- find Instagram/TikTok posts whose comments contain the target problem;
- propose a native response, educational contribution, AMA, guide or community-specific content;
- use the user's authorised founder/brand/community account for execution where possible.

The valuable automation is mostly:

```text
find the right conversation
  → understand context
  → choose the right message / offer / CTA
  → prepare the action
  → get approval where needed
  → measure downstream conversion
```

## Account model

The initial product discussion included the idea of maintaining pools of Telegram/Reddit accounts to
perform promotion. That hypothesis should be treated carefully.

A durable Partizan product should primarily support:

1. **connected client accounts** — the client authorises its own Instagram/Telegram/Reddit/etc.;
2. **founder/brand accounts** — real accounts representing the product or founder;
3. **real ambassador/operator accounts** — actual people/accounts explicitly authorised to represent
   the product;
4. **manual-assisted execution** where a platform does not expose a stable API for the desired action.

The system should not depend on fake account farms, coordinated undisclosed impersonation, mass spam or
technical ban-evasion as its core acquisition infrastructure. Apart from platform/policy risk, those
mechanisms make the business fragile and destroy attribution/learning quality.

This does **not** remove guerrilla marketing. It changes the optimisation target from "send as much as
possible before bans" to "find the highest-context conversation and make one useful, native action".

## Profile funnel and intermediate landing layers

A direct product link is not always the best CTA.

Partizan should be able to optimise a funnel such as:

```text
useful comment / community contribution
  → profile view
  → profile bio / pinned content
  → landing page
  → product / Telegram bot
```

An intermediate landing layer can legitimately provide:

- explanation before deep-linking into a bot/app;
- UTM/referral attribution;
- social proof;
- dynamic offer/message by source;
- A/B testing;
- retargeting/analytics where appropriate;
- consistent routing across platforms.

It should be treated as a conversion/measurement layer, not as cloaking designed to evade moderation.

## Platform-specific examples to explore in the channel matrix

### Telegram

Surfaces:

- public channels;
- groups;
- linked discussion chats;
- comments under channel posts;
- creators/admins;
- Telegram Ads inventory.

Possible tactics:

- Telegram Ads;
- direct paid post;
- admin partnership;
- affiliate deal;
- founder/community participation;
- community-specific content;
- profile funnel;
- referral/landing layer.

Open design question: how much execution can reliably be automated versus assisted/manual through an
authorised user account.

### Instagram

Surfaces:

- creators;
- Reels;
- posts;
- comments/conversations;
- audience clusters;
- Meta Ads inventory.

Possible tactics:

- Meta Ads;
- creator sponsorship;
- affiliate/creator seeding;
- own Reels/posts/Stories;
- founder/brand participation in relevant conversations;
- profile funnel;
- manual-assisted comments where APIs do not support arbitrary external posting.

Expected account model: client authorises its own professional/founder account for owned publishing.

### Reddit

Surfaces:

- subreddits;
- individual threads;
- comments;
- keyword/high-intent conversations;
- Reddit Ads inventory.

Possible tactics:

- Reddit Ads / conversation ads;
- founder posts/comments;
- useful answers in relevant threads;
- AMA / guides / community-specific content;
- partnerships with community owners where appropriate;
- profile/landing funnel.

The important opportunity unit is often a **specific live thread**, not merely a subreddit.

### Google

The opportunity unit is usually a **search query / intent cluster**, not a social community.

Possible tactics:

- Google Search Ads;
- landing pages;
- programmatic/long-tail SEO;
- comparison pages;
- calculators/tools;
- educational pages mapped to high-intent queries.

Partizan should compare auction-based paid acquisition with organic asset creation using the same
underlying intent map.

## New desired user-facing output

Instead of returning only "20 Growth Plays", Partizan should show a channel portfolio such as:

```text
Audience distribution
- Instagram: high opportunity
- Reddit: high opportunity
- Google: high-intent opportunity
- Telegram: medium/high niche opportunity
- YouTube: supporting opportunity
```

For each platform:

```text
Concrete opportunities found
  + available tactics
  + estimated price / test budget
  + expected effect range
  + confidence
  + automation level
  + account/integration requirements
```

Then Partizan recommends a **test portfolio**, not a single tactic.

Example:

```text
1. Reddit high-intent community test — $50
2. Instagram micro-creator seeding — $200
3. Owned Instagram Reels — low marginal spend
4. Telegram direct placement — $100
5. Google high-intent search test — $100
```

The Growth Manager should later reallocate money based on observed results.

## What changes in the current architecture

Existing building blocks remain valuable:

- ProductProfile;
- ICP Engine;
- Execution;
- Analytics Loop;
- Growth Manager;
- learning memory.

The biggest redesign is **Channel Hunter**.

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
  → Tactic catalog for that opportunity
  → price/effect/confidence/automation metadata
  → Growth Play / Experiment
```

`GrowthPlay` should become an executable tactic hypothesis tied to a concrete opportunity, rather than
being responsible for discovering what kinds of distribution exist.

## Next product-design task: channel matrix

Before building the web UI or adding more execution integrations, define the platform-by-platform
matrix.

For every platform we need to answer:

1. **Audience discovery** — how do we decide this audience is there?
2. **Surfaces** — what concrete object do we search for (channel, group, thread, creator, query, etc.)?
3. **Opportunity data** — what metadata/evidence must we collect?
4. **Standard paid tactics** — official ad mechanisms and economics.
5. **Direct paid tactics** — creator/admin/newsletter/partner access.
6. **Owned organic tactics** — what can be created/published through the client's accounts?
7. **Community/guerrilla tactics** — what high-context native actions are possible?
8. **Automation level** — full / approval-gated / assisted/manual.
9. **Required integration/account** — what the client must connect.
10. **Attribution** — how a user is tied back to the opportunity/experiment.
11. **Cost/effect model** — how Partizan estimates cost, reach, conversion and CAC before data exists.
12. **Risks/constraints** — operational and platform-specific limitations.

Initial platforms for the matrix:

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
revision.
