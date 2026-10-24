# FemDom Telegram: repair map gaps, media-only posts and group drafts (25 Oct 2026)

## Production evidence, last completed read-only eight-peer audit
See GitHub Actions run 40142515279. No messages were sent, no account joined a group.
- `bizarreclubmsk`: manually confirmed DIRECT; native latest inspected; legacy map reported `AMBIGUOUS_OR_MISSING_OPPORTUNITY`. It was absent from the nine-peer legacy cohort.
- `fem_tut`: manually confirmed DIRECT, but legacy map failed unique matching; current latest commentability is not established. The historical map had 10 rows for nine unique handles, so a duplicate is plausible but not established until an exact match count is recorded.
- `play_hard_community`: confirmed audience, but latest post #406 did not have enough readable text to ground a reply.
- `a_sfera`, `findom_gospoja0`, `poiskrabovirabin`: review-only drafts prepared; group-root messages were generic and not strong enough to ship without human evaluation.

## Repairs
- `seed_missing_reviewed_opportunity` is restricted to existing, explicitly reviewed FemDom handles with one current native Telegram entity and cached, user-confirmed DIRECT fit. An existing saved project ICP ID and a fresh native inspection are both required. New row starts as `RESEARCH_ONLY`, no action URL, no account/publishing rights; the ordinary latest-target bridge may then set only `NEEDS_PREFLIGHT`. Fixed deterministic ID, guarded duplicates, idempotency tests.
- If a handle has **multiple** legacy map entries, do **not** collapse them blindly or pick a random winner; return `DUPLICATE_SAVED_OPPORTUNITIES` including match count. Existing action references could be attached to those older IDs. Resolve from actual ID/provenance and read-only audit before deduplication.
- Persist `media_present` and the native media class name for the **single latest** Telegram message without downloading images or videos. Without readable caption/context, return `MEDIA_ONLY_NEEDS_VISUAL_REVIEW` or `SHORT_POST_NEEDS_CONTEXT_REVIEW`, with no LLM call, draft, account join or publishing action.
- Prompt v13 for `STANDALONE_POST` asks for one distinctive community-grounded conversational hook, not moralizing about trust, consent, boundaries or generic verification. Still only one draft, same project scope, no identity impersonation, invented attendance, clickbait or disguised ads; automatically cached on prompt-version fingerprint.

## Acceptance
CI must confirm: native latest-only semantics, no old post fallback, map append idempotence, rejection of stale/ambiguous/reviewless mappings, short/media text fail-closed and updated one-message prompt. A human still reviews the actual two new group drafts, checks destination rules and says «публикуй» before any send.

## Remaining live blockers
- The actual `fem_tut` duplicate count and whether one record has a stable native ID must be measured and reconciled without breaking historic references.
- `play_hard_community/406` needs a view of the media itself, not a language model guessing from a generic description. This bounded pipeline has no image-content retrieval.
- Current per-account permissions and promotional restrictions remain unverified; `NEEDS_PREFLIGHT` is not send authorization.
