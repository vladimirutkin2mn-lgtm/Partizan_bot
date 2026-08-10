# Instagram MVP — account and distribution model

## Decision summary

Instagram should not require the client to publish Partizan-generated organic content from the client's own account.

The MVP distinguishes four different account/distribution modes:

1. **Partizan Community Distribution** — Partizan-owned thematic Distribution Identities participate around relevant external creators/posts.
2. **Partizan Identity Maintenance** — Partizan may publish limited evergreen/native content on those identities so the profile is coherent and useful as a funnel.
3. **Client Paid Acquisition** — Meta/Instagram Ads run through the client's advertising account when the client chooses paid acquisition.
4. **Client-Owned Organic** — optional only; if the client explicitly connects a Professional account, Partizan may help publish Reels/posts/stories for that brand.

A fifth concept — a large Partizan-owned thematic media network whose accumulated followers/reach become a reusable commercial asset — is **Post-MVP**.

## Why media-network growth is Post-MVP

There is a real strategic upside to building owned thematic Instagram media assets:

- audience does not need to be rebuilt from zero for every client;
- successful thematic accounts can become durable distribution inventory;
- follower/reach history can become a Partizan-owned asset;
- clients who do not want AI-generated Reels in their own brand feed can still access organic Instagram distribution.

However, making this a first-class MVP objective creates a second difficult product problem in parallel with the main Partizan hypothesis.

Partizan would have to solve:

- which verticals deserve dedicated media accounts;
- how many accounts are needed;
- how to keep each feed coherent while serving many unrelated client requests;
- editorial positioning and content quality over time;
- follower growth and retention;
- account portfolio management;
- avoiding theme dilution when multiple products share one broad account;
- deciding how client-specific promotion affects the accumulated audience;
- content-production operations at scale.

Example of the theme-dilution problem:

```text
@relationship.tools
  → tarot product
  → dating app
  → AI girlfriend
  → family psychology service
  → relationship course
```

Even if all belong to a broad relationship vertical, the accumulated audience may not remain equally valuable for every next client.

Partizan should therefore prove the simpler hypothesis first:

> Can we acquire real users by identifying relevant Instagram distribution surfaces and executing/optimising distribution better than the client could alone?

Only after repeated vertical demand and conversion data are visible should Partizan deliberately build media assets in the strongest verticals.

## Instagram MVP account model

### 1. Partizan Community Distribution — Partizan-owned accounts

Partizan maintains a small pool of broad thematic **Distribution Identities**.

Illustrative themes:

- AI & Tech;
- Relationships & Lifestyle;
- Business & Startups;
- Finance & Crypto;
- Wellness;
- Entertainment.

These accounts are infrastructure for distribution, not yet standalone media businesses.

Their main role:

```text
find relevant external creator/post
  → choose a suitable Partizan Distribution Identity
  → publish/prepare a native community action where operationally possible
  → user sees the identity
  → profile funnel
  → client product
```

The profile itself is an acquisition asset. It should have coherent positioning, useful profile information and an attributable destination.

### 2. Partizan Identity Maintenance — limited owned content

Partizan may publish limited evergreen/native Reels/posts on its Distribution Identities to keep profiles coherent, current and useful.

This content is **not** treated as the primary MVP growth engine and Partizan does not need to optimise these accounts for follower growth as a core KPI.

Primary purpose:

- maintain a credible thematic profile;
- give profile visitors useful context;
- support the profile funnel;
- maintain normal account activity/history;
- test lightweight owned content without making media growth a separate business objective.

MVP KPI is still downstream acquired users, activation and paid conversion — not follower count.

## Client-side account modes

### 3. Client Paid Acquisition — client's ad account

If the client wants Instagram/Meta paid acquisition, the client connects the relevant advertising account/business assets.

Partizan can then manage:

```text
budget
  → audience/targeting hypotheses
  → creative variants
  → campaigns/ad sets/ads
  → spend and conversion data
  → CAC/CPA/ROAS
  → Growth Manager decision
```

The paid campaign represents the client's product/brand. This is separate from Partizan-owned community identities.

The client should be able to use Partizan without connecting a personal Instagram profile for community distribution.

### 4. Client-Owned Organic — optional

Some clients already own a useful Instagram Professional account and may want Partizan to help operate it.

If explicitly connected/authorised, Partizan can later support:

- Reels;
- posts/carousels;
- stories where integration permits;
- inbound comment handling;
- organic performance analytics.

This mode is optional because:

- some clients have strict brand identity/creative standards;
- AI-generated Reels may not fit their feed;
- some clients do not want to delegate publishing rights;
- some clients do not have a useful Instagram presence;
- community distribution should work without this integration.

The onboarding principle should be:

> Partizan can start community distribution without your Instagram account. Connect brand-owned assets only when you want Partizan to operate them.

## Post-MVP — Partizan Media Network

A **Partizan Media Network** is a different asset from a Distribution Identity.

### Distribution Identity

Purpose:

- execute community distribution;
- maintain a coherent profile;
- provide a profile funnel;
- participate around relevant audiences.

It does **not** need a large follower base to create value.

### Media Asset

Purpose:

- repeatedly publish content;
- intentionally grow followers/reach;
- become reusable owned inventory;
- lower future acquisition cost in a proven vertical.

The Media Asset model should be created only when Partizan data shows repeated demand and attractive economics in a vertical.

Example future loop:

```text
Partizan serves many clients
  → repeated demand appears in relationships/dating
  → Instagram experiments repeatedly convert in this vertical
  → Partizan intentionally builds Relationship Media Network
  → owned organic reach grows
  → future compatible clients can use that inventory
```

This converts product learning into a distribution moat instead of guessing verticals in advance.

## Instagram MVP structure

### Engine A — Instagram Paid

Account ownership: **client ad account**.

Purpose:

- Meta/Instagram Ads;
- attributable paid acquisition;
- creative and audience testing;
- spend/CAC optimisation.

Included in MVP.

### Engine B — Instagram Community

Account ownership: **Partizan-owned Distribution Identities**.

Purpose:

- identify relevant external creators/accounts/posts;
- use a thematically suitable Partizan identity;
- perform or prepare native community actions;
- route interest through the Partizan identity/profile funnel;
- attribute downstream client conversion where possible.

Included in MVP, subject to the execution method supported by the platform/integration.

### Engine C — Client-Owned Organic

Account ownership: **client**.

Purpose:

- publish on the client's own brand account when explicitly authorised.

Optional / secondary MVP capability, not required for onboarding.

### Engine D — Partizan Media Network

Account ownership: **Partizan**.

Purpose:

- intentionally grow owned thematic audiences as reusable inventory.

**Post-MVP.**

## MVP scope table

| Capability | Account | MVP |
|---|---|---|
| Meta / Instagram Ads | Client ad account | Yes |
| Relevant creator/account discovery | None | Yes |
| Community actions around relevant external content | Partizan Distribution Identity | Yes |
| Profile funnel | Partizan Distribution Identity | Yes |
| Limited evergreen content to maintain Partizan identities | Partizan Distribution Identity | Yes |
| Grow large Partizan thematic follower bases as core KPI | Partizan Media Asset | No — Post-MVP |
| Reels/posts on client's own Instagram | Client account | Optional |
| Require client personal account for community distribution | Client | No |
| Creator negotiation/outreach | N/A | Post-MVP |
| Paid creator integrations | N/A | Post-MVP |
| Cold-DM acquisition | Partizan/client | No |

## Product architecture consequence

Do not model every Partizan-owned social account as the same thing.

We likely need an explicit distinction such as:

```text
DistributionIdentity
  role = COMMUNITY_OPERATOR

DistributionIdentity / MediaAsset
  role = OWNED_MEDIA
```

or separate entities later if their lifecycle/KPIs diverge materially.

For MVP, only `COMMUNITY_OPERATOR` is required.

## Primary Instagram MVP learning questions

1. Can Partizan identify external Instagram accounts/posts whose audiences are relevant to the client's ICP?
2. Can Partizan-owned Distribution Identities turn that relevance into attributable product traffic and paid users?
3. How does this CAC compare with Meta Ads?
4. Which broad Distribution Identity themes repeatedly perform well?
5. Do certain verticals show enough repeated demand and organic performance to justify a dedicated Media Network later?

The fifth question is the decision gate for the Post-MVP media-network investment.
