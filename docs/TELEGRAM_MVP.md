# Telegram MVP

## Product decision

For the Telegram Community MVP, Partizan should **not** over-invest in message-level intelligence.

The primary acquisition hypothesis is simpler:

> Find Telegram channels and groups whose audience matches the client's ICP, participate from Partizan-owned Distribution Identities, route interest through the operator profile, and learn which communities produce real acquired users.

The optimisation target is therefore **which communities are worth operating in**, not "which exact message is the perfect lead".

## Two Telegram acquisition engines

Telegram remains split into two independent engines:

```text
Telegram Paid
Telegram Community
```

They should be attributed and learned separately.

### Telegram Paid

Included in MVP:

- discover relevant public channels / audience clusters;
- use Telegram Ads where the destination and inventory are eligible;
- create attributable bot/product deep links;
- measure start → activation → paid → CAC;
- let Growth Manager reallocate budget based on observed economics.

### Telegram Community

Included in MVP:

- find relevant channels that have active comments / linked discussions;
- find relevant public groups;
- assign a Partizan-owned Telegram Distribution Identity;
- create simple native comments, replies or standalone contributions;
- use the operator profile as the default conversion funnel rather than putting a direct product link into every message;
- record removals/restrictions and downstream conversions;
- learn which communities are productive and which should be stopped.

## Core modelling simplification

### Opportunity = community

For the Telegram Community MVP, a `DistributionOpportunity` should normally be a **channel or group**, not an individual message.

Examples:

```text
Opportunity
- type: channel_with_comments
- channel: @example_channel
- topic: astrology
- language: English
- audience relevance: 91/100
- activity: high
- comments available: yes
- assigned identity: Partizan Relationship Scout
- status: active
```

or:

```text
Opportunity
- type: group
- group: @example_group
- topic: AI founders
- language: English
- audience relevance: 88/100
- activity: high
- posting available: yes
- assigned identity: Partizan AI Scout
- status: active
```

### Distribution Action = one concrete post/comment/reply

A specific execution inside the community should be represented separately as a `DistributionAction`.

Examples:

```text
2026-08-10 — comment under a fresh channel post
2026-08-12 — standalone group contribution
2026-08-14 — reply to a recent group message
```

Suggested fields:

```text
id
opportunity_id
distribution_identity_id
action_type: comment | standalone_post | reply
source_post_or_message_id: optional
content
created_at
published_at
status
removal_or_restriction_signal
attribution_route
experiment_id
```

This separation lets Partizan learn both:

- whether the **community** is valuable;
- which **action types / message patterns** work inside that community.

## Surface 1 — channels with comments

Partizan should search for relevant Telegram channels and determine whether there is an accessible linked discussion / comments surface.

The channel itself is the opportunity.

Execution flow:

```text
relevant channel
  → find a recent post with comments enabled
  → generate a short, topically relevant contribution
  → publish from the assigned Partizan Distribution Identity
  → profile funnel
  → product attribution
```

The system does not need sophisticated semantic lead detection. It only needs enough recent context to avoid producing an obviously irrelevant comment.

Default behaviour should not rely on putting a direct product/bot link into the comment.

## Surface 2 — groups

Partizan should search for relevant public groups.

The group itself is the opportunity.

Two action types are enough for MVP:

### Standalone contribution

Post a useful, relevant message into the group without waiting for a perfect trigger message.

Examples of formats:

- useful observation;
- practical tip;
- mini-guide;
- question that can start a useful discussion;
- concise educational contribution related to the product domain.

### Reply

Reply to a recent message when that is the most natural way to participate.

The MVP should **not** require finding a high-intent individual user or scoring every message in the group. Recent context is only used to keep the reply coherent.

## Minimum data to collect per community

For Telegram MVP, the community record should stay pragmatic.

Suggested fields / features:

- surface type: channel_with_comments | group;
- title;
- URL / Telegram identifier;
- topic/category;
- language;
- approximate size;
- recent activity / freshness;
- comments available for channels;
- posting/reply capability for groups;
- audience / ICP relevance score;
- assigned Distribution Identity;
- action history;
- recent action frequency;
- removals / restrictions / failed actions;
- attributed bot starts / visits;
- activations;
- paid users;
- revenue where available;
- CAC / cost per activated user where spend can be allocated;
- status: candidate | testing | active | paused | stopped.

## Community scoring

Initial scoring should be community-level and intentionally simple.

Candidate dimensions:

- topical / ICP relevance;
- language fit;
- audience size;
- activity/freshness;
- ability to participate (comments/posting available);
- historical conversion performance once data exists;
- moderation/removal friction as a negative signal.

The score should not depend on deep analysis of every individual message.

## Distribution cadence and learning

Partizan should optimise a **portfolio of communities**, not message volume.

Example test cycle:

```text
300 communities discovered
  → 30 selected for bounded testing
  → 1–2 Distribution Actions per community
  → collect starts / activations / paid / removals
  → stop weak communities
  → continue promising communities
  → scale presence in winners
```

Growth Manager should make decisions using actual outcomes.

Example:

```text
19 communities: no product starts → STOP
6 communities: starts but no activation → MODIFY / CONTINUE selectively
3 communities: activated users → CONTINUE
2 communities: paid users at acceptable economics → SCALE
```

This is more valuable for MVP than building a complex system that tries to identify the perfect message to answer.

## Profile funnel

For Telegram Community, the default funnel remains:

```text
comment / group contribution
  → user becomes curious about the Distribution Identity
  → profile view
  → profile bio / destination
  → landing / Telegram bot / client product
  → activation
  → paid
```

The profile is therefore an acquisition asset and should be tracked/configured as part of the Distribution Identity.

## Not MVP

Telegram Community MVP should not include:

- deep NLP analysis of every message in a group;
- per-user purchase-intent scoring;
- perfect-trigger-message search;
- negotiation with channel administrators;
- direct paid placements with admins;
- follower/subscriber boosting;
- requirement to use the client's personal Telegram account;
- disposable fake-account farms;
- mass direct-link spam;
- technical ban-evasion infrastructure.

## MVP flow

```text
Product + ICP
  → find Telegram channels/groups
  → score community relevance
  → select test communities
  → assign Partizan Distribution Identity
  → choose action type: comment | standalone | reply
  → generate simple native content
  → publish within operating limits
  → profile funnel
  → start / activation / paid attribution
  → learn community economics
  → SCALE / CONTINUE / MODIFY / STOP
```

## Architecture consequence

The Telegram-specific data model should distinguish:

```text
DistributionOpportunity = community (channel/group)
DistributionAction = concrete comment/post/reply
DistributionIdentity = Partizan-owned operator account
Experiment = bounded test tying actions and attribution together
```

This replaces the earlier assumption that every specific live Telegram message should itself be the core acquisition opportunity.