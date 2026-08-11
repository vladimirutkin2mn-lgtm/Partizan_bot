# Instagram Community MVP — surfaces, actions and attribution

## Decision summary

The Instagram Community MVP should be intentionally simple.

We do **not** try to identify the perfect individual user, perfect comment thread or exact purchase-intent moment. The primary optimisation unit is the **external creator/account** whose audience is relevant to the client's ICP.

Core model:

```text
DistributionOpportunity = external Instagram creator/account
DistributionAction = comment under a fresh relevant Reel/Post
DistributionIdentity = Partizan-owned thematic Instagram account
CampaignSlot = one active client campaign assigned to an identity for a bounded period
Experiment = batch of creators/actions measured together
```

The primary learning question is:

> Which creators/audience clusters, operated through which Partizan Distribution Identities, produce real product visits, activations and paid users?

## 1. Community surfaces

The MVP focuses on two concrete Instagram surfaces.

### Surface A — external creator/account

This is the main `DistributionOpportunity`.

Examples:

- relationship coach;
- astrology creator;
- AI tools page;
- startup creator;
- crypto education account;
- wellness creator.

The creator/account is scored as a whole based on whether its audience appears relevant to the requested ICP.

### Surface B — fresh Reel/Post from an approved creator

A concrete Reel/Post is **not** the persistent opportunity entity. It is an execution surface for a `DistributionAction`.

Partizan should prefer recent, active content with comments available and apply only a lightweight context/relevance check.

The MVP does not require deep analysis of thousands of comments or individual users.

## 2. Creator/account discovery

Partizan starts from the product and requested audience and expands into thematic search concepts.

Example for an English-speaking relationship/astrology product:

```text
relationship advice
breakup advice
dating coach
tarot
astrology
zodiac relationships
self-reflection
```

For each candidate external account, useful MVP metadata includes:

- username / URL;
- profile description;
- inferred theme/vertical;
- language;
- approximate audience size where available;
- recent publishing activity;
- recent Reels/posts available for action;
- approximate engagement/activity signals where available;
- ICP relevance score;
- prior Partizan action history;
- moderation/removal/problem history;
- attributed campaign outcomes associated with that creator/audience cluster.

The goal is not to perfectly understand the creator. The goal is to decide whether this audience is worth testing.

## 3. Opportunity scoring

`InstagramOpportunityScore` should be creator/account-level.

Possible MVP factors:

- topical relevance to ICP;
- language fit;
- audience scale proxy;
- recent activity;
- availability of fresh commentable content;
- engagement/activity proxy;
- historical Partizan conversion data for this creator or similar creators;
- operational friction / removal history.

A simple deterministic weighted score is sufficient initially.

Do **not** add per-user purchase-intent scoring in MVP.

## 4. Main Community Distribution Action

The primary guerrilla/community primitive is:

> **Comment under a relevant external Reel/Post from a Partizan-owned Distribution Identity.**

Default flow:

```text
relevant creator/account
  → choose fresh relevant Reel/Post
  → read enough context to avoid an irrelevant comment
  → choose Partizan Distribution Identity
  → generate native contextual comment
  → execute/prepare comment
  → user notices identity
  → profile funnel
  → client product
```

The MVP should not depend on:

- cold DMs;
- mass likes;
- follow/unfollow loops;
- replies to large numbers of individual users;
- deep comment-thread analysis;
- direct product links in every external comment;
- creator negotiations or paid integrations.

The value comes from choosing relevant creators/audience clusters and measuring whether those surfaces convert.

## 5. Lightweight post/reel context

Partizan still needs enough context to avoid writing something obviously unrelated.

For an approved creator, Partizan can inspect a small set of fresh Reels/posts and reject obvious mismatches.

Example:

```text
Creator theme: relationships

Reel: "5 signs he is not interested"       → suitable
Reel: "how to move on after a breakup"     → suitable
Sponsored shoe post                         → skip
Personal family photo unrelated to theme    → skip
```

This is a cheap execution filter, not a separate message-level intelligence product.

## 6. Partizan Distribution Identities

Community comments are made/prepared through **Partizan-owned thematic accounts**, not the client's personal Instagram account.

Illustrative broad identities:

- AI & Tech;
- Relationships & Lifestyle;
- Business & Startups;
- Finance & Crypto;
- Wellness;
- Entertainment.

These identities are `COMMUNITY_OPERATOR` infrastructure, not yet large owned media assets.

Selection inputs can include:

- thematic fit;
- language;
- profile positioning;
- account health;
- recent action history;
- prior conversion performance;
- campaign assignment/conflicts.

## 7. Profile funnel

The default funnel is:

```text
external comment
  → Partizan Distribution Identity profile
  → persistent profile destination
  → Partizan routing/landing layer
  → client product
  → activation
  → paid
```

A Distribution Identity should have stable thematic positioning rather than being renamed/rebuilt for every client.

Illustrative profile positioning:

```text
@relationship.tools
Apps, ideas & tools for navigating relationships.
Testing interesting relationship products.
Current tool ↓
```

The Instagram profile URL/destination can remain stable while the Partizan routing layer changes the active client destination.

The routing layer is for conversion and attribution, not moderation evasion.

## 8. Campaign Slot

A Partizan Distribution Identity should not simultaneously route community traffic to multiple unrelated client products if this destroys attribution and profile coherence.

Introduce a `CampaignSlot`:

```text
Distribution Identity
@relationship.tools

Campaign Slot
Client: Oracle
Duration: bounded test window
Destination: Oracle attribution route

Actions
20 creators
40 comments

Observed outcomes
profile/landing visits
activations
paid users
revenue
```

For MVP, one active client campaign per identity/slot is the clean default.

A later version may support multiple concurrent destinations only if attribution and UX remain clear.

## 9. Attribution model

Because the default external comment does not contain a unique direct product link, perfect comment-level attribution is not realistic for the MVP.

Therefore the primary attribution unit should be **campaign/batch-level**.

Example:

```text
Identity: @relationship.tools
Client campaign: Oracle
Window: 7 days
Creators tested: 20
Comments/actions: 40

Attributed downstream outcomes:
143 routing-page visits
37 activations
6 paid users
```

This is sufficient to calculate experiment economics and compare Instagram Community against other channels.

We can still store each creator/action timestamp and later infer which creator clusters correlate with traffic spikes, but that should be treated as approximate evidence unless a stronger attribution mechanism exists.

## 10. Experiment and Growth Manager unit

Growth Manager should not optimise individual comments as if each had perfect attribution.

The MVP experiment unit is a **bounded creator/action batch**.

Example:

```text
Experiment: Oracle — Instagram Relationships
Distribution Identity: @relationship.tools
Creators: 20
Actions: 40 comments
Period: 7 days

Result:
visits
activations
paid
revenue
operational removals/restrictions

Decision:
STOP / CONTINUE / MODIFY / SCALE
```

The system learns which creator/audience clusters and identities are worth further testing.

## 11. Identity Maintenance

Partizan may publish a limited amount of evergreen/native content on its own Distribution Identities so the profile is coherent and useful when someone opens it after seeing a comment.

Examples:

- lightweight thematic Reel;
- carousel;
- useful observation;
- evergreen educational post.

This is **Identity Maintenance**, not a follower-growth engine.

MVP KPI remains downstream acquisition, not follower count.

A dedicated Partizan Media Network that intentionally grows large owned audiences remains Post-MVP and is described in `docs/INSTAGRAM_MVP.md`.

## 12. Client-owned modes remain separate

### Instagram Paid

Account: client's advertising/business assets.

```text
Meta Ads
  → client product
  → CAC / CPA / ROAS
```

This is a separate acquisition engine from Partizan Community.

### Client-Owned Organic

Account: client's Professional/brand account, only if explicitly connected.

Partizan may help create/publish organic content later, but this is optional and not required for onboarding or community distribution.

## 13. Execution architecture note

The product architecture should separate:

```text
CreatorDiscovery
  → OpportunityScoring
  → FreshContentSelection
  → CommentGeneration
  → DistributionIdentitySelection
  → ExternalExecutionAdapter
  → CampaignAttribution
```

The execution adapter is intentionally a separate concern because commenting on arbitrary third-party Instagram media is not the same primitive as publishing/managing media on our own connected Professional accounts.

We should validate community economics before investing heavily in complex external execution infrastructure.

## 14. MVP scope

| Capability | MVP |
|---|---|
| External creator/account discovery | Yes |
| Creator/account-level relevance scoring | Yes |
| Fresh Reel/Post selection | Yes |
| Lightweight content-context check | Yes |
| Deep comment/user intent analysis | No |
| Main community action = comment | Yes |
| Partizan-owned Distribution Identity | Yes |
| Profile funnel | Yes |
| Campaign Slot per identity | Yes |
| Batch/campaign-level attribution | Yes |
| Identity Maintenance content | Yes, limited |
| Comment-level perfect attribution | No |
| Cold DM acquisition | No |
| Creator negotiation/outreach | Post-MVP |
| Paid creator integrations | Post-MVP |
| Partizan Media Network/follower growth | Post-MVP |
| Instagram/Meta Ads | Yes, separate client-paid engine |
| Client-owned organic publishing | Optional |

## 15. Primary MVP learning loop

```text
find 100 relevant creators/accounts
  → select 20 for a bounded test
  → execute ~40 comments/actions from one suitable Distribution Identity
  → measure routing visits / activation / paid / operational friction
  → STOP weak audience clusters
  → CONTINUE promising clusters
  → SCALE or broaden winners
```

The Instagram Community MVP should therefore optimise **which creators and audience clusters work**, not try to predict the perfect individual user or perfect comment moment.
