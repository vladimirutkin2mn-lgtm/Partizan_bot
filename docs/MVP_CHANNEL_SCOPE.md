# MVP channel scope

## Decision

Partizan has two different channel concepts and they must not be conflated:

1. **Research surfaces** — places Partizan can investigate to find audiences, creators, communities, partners, media and demand without requiring the customer's account access up front.
2. **Execution ecosystems** — platforms with first-class Partizan distribution models, adapters, permissions, attribution and execution controls.

The MVP execution domain deliberately remains focused on four ecosystems:

1. Telegram
2. Instagram
3. Reddit
4. TikTok

This narrow execution scope does **not** mean Partizan's customer-acquisition research should look as if it is limited to those four logos.

## Research surface

Customer-facing research may consider a broader public-web opportunity universe, including:

- creators and influencers across YouTube, X, blogs and other public profiles;
- newsletters, podcasts and specialist media;
- affiliate and partnership candidates;
- Google Search / SEO demand and query opportunities;
- directories, review sites and niche websites;
- Discord communities, forums and other public groups;
- Telegram, Instagram, Reddit and TikTok opportunities.

Research access and execution access are separate. Public research should not require the customer to connect an account merely so Partizan can decide whether a surface is relevant.

A surfaced research opportunity is **not** a promise that Partizan can automatically post, buy media or spend there. If execution needs an account connection, paid placement, partner approval or manual handoff, the product must make that boundary explicit.

## Why execution scope is intentionally narrow

The MVP goal is not to implement an autonomous adapter for every possible marketing channel.

The MVP goal is to prove the core product loop:

```text
Product + ICP
  → research across plausible customer-acquisition surfaces
  → concrete opportunities
  → select an executable path
  → attributed experiments
  → activation / paid users / CAC
  → Growth Manager reallocates effort and budget
```

Telegram, Instagram, Reddit and TikTok provide enough diversity to validate the controlled execution architecture:

- community-first distribution;
- creator/audience-surface distribution;
- rule-governed communities;
- Partizan-owned Distribution Identities;
- paid platform advertising;
- profile/routing funnels;
- first-class Partizan-owned organic content experiments on TikTok;
- batch/campaign/action-level attribution patterns;
- cross-channel CAC comparison.

Adding first-class execution adapters for more ecosystems before this loop works would increase integration, policy and control surface without materially improving MVP validation.

## MVP execution platforms

### Telegram

Core modes:

- Telegram Community;
- Telegram Paid.

Persistent Community Opportunity:

```text
channel / group
```

### Instagram

Core modes:

- Instagram Community;
- Instagram Paid;
- optional Client-Owned Organic;
- limited Identity Maintenance.

Persistent Community Opportunity:

```text
external creator / account
```

Partizan Media Network remains Post-MVP.

### Reddit

Core modes:

- Reddit Community;
- Reddit Paid.

Persistent Community Opportunity:

```text
subreddit
```

`CommunityPolicy` is mandatory before commercial execution.

### TikTok

Core modes:

- TikTok Community;
- Partizan Organic Experiments;
- TikTok Paid;
- optional Client-Owned Organic.

Persistent Opportunity:

```text
content / topic cluster
```

Partizan Media Network remains Post-MVP.

## Post-MVP first-class execution adapters

The following ecosystems do not need dedicated autonomous execution adapters to unblock MVP launch:

```text
YouTube
Google Search
X
Discord
Newsletters
Niche sites
Forums
Other new platforms
```

They may still appear as research surfaces before they become first-class `DistributionPlatform` values. Moving one of them into autonomous execution requires an explicit implementation with permissions, policy handling, attribution and fail-closed controls.

## Re-entry criteria for a new execution platform

A research surface should be promoted into a first-class execution ecosystem only when at least one of the following is true:

- repeated client demand is visible;
- Audience Intelligence repeatedly finds meaningful audience concentration there;
- the existing execution channels leave a material ICP segment unreachable;
- expected CAC is meaningfully better than current channels;
- the platform offers a uniquely valuable acquisition mechanic;
- implementation cost is low enough relative to expected incremental value.

## Product-design consequence

Customer UI must answer two different questions separately:

- **Where can Partizan look for customers?** — broad research universe.
- **What access can Partizan use to execute?** — only supported, permissioned execution paths.

The product must never use a short list of connected integrations as a proxy for the full acquisition research surface.
