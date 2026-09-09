# Reddit research + CommunityPolicy — Phase 4

This document describes the Phase 4 implementation tracked by #252 and PR #262.

## Scope

Phase 4 is read-only research and policy intelligence. It does **not** add Reddit publishing credentials, OAuth publishing, voting, moderation, direct messaging, joining, account automation, or a client-owned publish adapter.

The persistent learning unit remains the subreddit:

```text
DistributionOpportunity = subreddit
ActionTarget = fresh relevant Reddit thread when COMMENT/REPLY is selected
CommunityPolicy = hard execution gate
```

## Research boundary

The implementation uses the existing generic indexed `SearchProvider` for public evidence. Reddit publishing credentials are neither required nor enabled by research.

A direct Reddit Data API connector is intentionally not introduced by this phase. Production-appropriate API/commercial access, identity requirements, and any later publishing permissions are separate readiness decisions and must remain fail-closed until explicitly approved and configured.

Research output is operational evidence for Partizan decisions. It is not introduced as model-training data.

## CommunityPolicy provenance

A stored Reddit `CommunityPolicy` carries:

```text
source
research_status
last_checked_at
fresh_until
evidence
confidence
commercial_participation_allowed
self_promotion_allowed
links_allowed
product_mentions_allowed
standalone_posts_allowed
comments_allowed
disclosure_required
special_promotion_windows
ai_content_constraints
```

Automated indexed research persists one of two research states:

- `VERIFIED` — evidence exists and every modeled permission/disclosure field has an explicit signal;
- `PARTIAL` — one or more permission fields remain ambiguous/unknown.

`PARTIAL` is deliberately fail-closed and cannot authorize Reddit community execution.

Manual policy review remains represented as `MANUAL` and receives the same freshness window when stored through the control plane.

## Freshness contract

Policy freshness is bounded to seven days in Phase 4.

Execution is blocked when:

- `last_checked_at` is missing;
- the check timestamp is materially in the future;
- the policy is older than seven days;
- `fresh_until` has passed;
- research state is not `MANUAL` or `VERIFIED`.

This means policy fit is a hard execution gate rather than merely a ranking feature.

## Policy evidence parsing

The research proposal models explicit signals for:

- commercial participation;
- self-promotion;
- links;
- product mentions;
- standalone promotional posts;
- promotional comments/replies;
- disclosure requirements;
- designated promotion surfaces/windows such as megathreads;
- AI-generated-content prohibition/disclosure constraints.

Ambiguous text stays `UNKNOWN`. The system does not infer permission from silence.

## Thread action-target freshness

Comment/reply targets are accepted only when they are concrete public Reddit thread URLs under the selected subreddit and their recency is verifiable.

Supported freshness evidence is intentionally narrow:

- provider metadata such as `published_at`, `created_at`, or `date` that parses as a timestamp; or
- an explicit relative-time source signal such as `2 days ago`.

The Phase 4 maximum thread age is seven days.

Targets are labeled:

```text
FRESH
STALE
UNVERIFIED
```

Only `FRESH` targets are eligible for Reddit COMMENT/REPLY drafting. Raw opportunity evidence is not used as a Reddit thread fallback because it does not carry the verified freshness contract.

## Ranking

Reddit opportunities receive a composite research score using:

```text
55% relevance
20% fresh activity
15% policy fit
10% prior measured outcomes
```

No prior outcome history is treated neutrally rather than as failure. Policy ambiguity drives policy-fit contribution down, but execution remains independently blocked by the CommunityPolicy gate.

## Failure behavior

Research-provider failure returns a partial enrichment result and preserves previously stored discovery evidence.

No provider exception grants execution permission. Missing policy evidence, stale policy, ambiguous policy, and missing fresh thread targets all fail closed.

## Acceptance boundary

Code/test acceptance for Phase 4 can be merged independently of production research evidence. Issue #252 must remain open until at least one real production research run against real subreddits is verified with non-secret evidence.

A production research verification should capture, without credentials or private data:

- discovered subreddit identity/URL;
- policy evidence source(s);
- `last_checked_at` / `fresh_until`;
- research state;
- at least one fresh thread target when available;
- ranking components;
- confirmation that no Reddit publishing credential/readiness was enabled by the research path.
