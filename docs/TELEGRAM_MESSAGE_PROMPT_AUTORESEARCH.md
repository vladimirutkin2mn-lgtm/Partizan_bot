# Telegram message quality: source context and AutoResearch

## Production drafting contract

- Telegram CHANNEL_COMMENT and GROUP_REPLY receive one versioned English prompt
  and return one message in the language/register of the **exact latest native
  target**. Marketing brief, audience fit and community description are not
  evidence of what the latest message says.
- The normal action composer omits historical `recent_context`, discovery
  evidence, and bulky product intake briefs from the LLM user payload.
- The bounded FemDom review preview preserves a verified latest native message
  up to 900 characters, instead of slicing a fully serialized JSON request.
  Older, mismatched, unreadable or stale targets fail before generation.
- This is still **drafting**, never Telegram publication. Platform permission,
  commercial rules, identity access, deduplication, and autonomous posting opt-in
  remain separate execution checks.

## Prompt-version evaluation inside Growth AutoResearch

The `GrowthAutoResearchService.evaluate_telegram_message_prompts` method now
accepts paired **human-reviewed** draft scores for the same target cases, comparing
an existing prompt version and a challenger. For each case:

1. Take a saved verified latest post with readable text, plus product and community
   context. Do not re-run broad community discovery just to test the prompt.
2. Prepare **one draft per prompt version** offline/review-only. Never submit the
   result to Telegram. This benchmark may require one additional model call per
   challenger; normal production drafting remains one call.
3. Human review scores the two actual drafts against the SAME target: target
   specificity (0–4), naturalness (0–4), usefulness (0–4), conversion fit (0–4).
   Record whether the message is grounded, in the right language, transparent
   about material promotion, and free of unsafe content. Record prompt+completion
   tokens for each draft.
4. Pass at least six unique paired reviews to `evaluate_telegram_message_prompts`.
   Review cases should cover different topics, channel comments and group replies,
   and should be judged against the full actual target text. Do not use invented
   example messages or repeated posts to claim a performance improvement.
5. The benchmark rejects any challenger with a failed grounding, language,
   transparency or content-safety check, rejects >10% aggregate token growth and
   requires a mean quality lift of at least 0.3 on the 0–4 scale.
   Results below six pairs are `INSUFFICIENT_PAIRED_REVIEWS`.
6. A successful comparison is `CANDIDATE_FOR_MANUAL_PROMOTION`, **not** a
   production prompt switch. It is a proposal for review, not proof of conversion.
   Actual clicks/signups/paid conversions can be tied to `prompt_version` only
   after verified execution and sufficient attributed outcomes.

Only version identifiers, aggregate grades, aggregate token cost, and a hashed
evaluation ID enter the saved AutoResearch result; raw texts, target URLs and case
IDs are deliberately not persisted. Evaluations can be repeated idempotently.

## Known limitation

This addition provides a reproducible evaluation gate, but does not yet
automatically retrieve saved production drafts, have an independent judge score
them, or measure conversion uplift. No real posts were generated, published, or
tested against live Telegram during this code change. Do not promote a challenger
based on synthetic unit-test review labels.
