# One-call community voice generator: first implementation

Date: 2026-10-27. This changes organic Telegram action drafting, **not** publication.

## Why change

The previous combined request contained `ACTION_DRAFT_SYSTEM_PROMPT` rules
1–12, a 6k-character generic community-marketing skill, and v15.1. Several
sentences in those layers encouraged formulaic "helpfulness" and a
"professional" register even for informal communities. The user explicitly
requested removal of old creative rules 8, 10 and 12 and the proposed
"only verified facts" sentence. This is **not** removal of external
compliance, publishing approval or truthfulness checks.

## What actually changed

- Replace the 12-rule general creative prompt with a concise always-conversion
  instruction. No special adult-oriented "professional" instruction.
- For organic Telegram, use a **new, versioned** `COMMUNITY_CREATIVE_GUIDANCE`
  instead of layering the generic community-marketing skill over v15.1.
  Kept old `COMMUNITY_MESSAGE_GUIDANCE` unmodified as a historical
  review-only baseline. Do NOT confuse the new main composer with the v15.1
  standalone preview; they have different prompt versions.
- Pass up to 650 characters of product description, `value_proposition`,
  `usp`, `product_type`, selected `known_audience`, use cases, and language
  to the organic generator. `input_brief` and old community message histories
  remain excluded. This improves the model's chance of understanding a
  product's AI/persona characteristics without inventing those facts.
- Voice is inferred from product + audience + community **inside the same
  generation**. No extra LLM call, no manually entered dominatrix persona,
  no FemDom-specific condition, no second rewriting/humanizer step.
- `voice_variant_id` supports bounded alternatives: adaptive, assertive,
  playful, warm, direct, dry_wit. Default production is adaptive; the
  `GrowthVariantSpec` field allows these alternatives to be represented as
  independent research variables. The ordinary action preparation path does
  not automatically select a challenger; this is not a completed live
  AutoResearch-to-generative-routing integration.
- Paired community prompt evaluations may now include 0–4 ratings for
  author curiosity and voice consistency. Historical studies keep the old
  weighting; mixed scoring rubrics are rejected. The result still needs
  paired human reviews, at least six matched targets and manual promotion.
- Required commercial / AI content disclosure flags are checked after
  draft generation (fail closed if the applicable policy requires them),
  not inserted as style-directing creative instructions. Existing
  `AI_CONTENT_PROHIBITED` precheck remains. Publishing permission,
  account access, moderation and platform safety remain separate.

## Not yet demonstrated

The automatic voice derivation currently happens within the one main LLM
request; there is not yet a persistent inferred, reviewable BrandVoiceProfile.
The connected Telegram account's existing bio/role is deliberately
excluded from the creative prompt. It belongs to profile conversion validation,
not to style inference. Do not invent identity or claim real account awareness.

No factual evidence of quality or CAC improvement yet. The review-only
v15.1 trial remains the control. A production rollout of this prompt
should be kept separately identifiable from a baseline and measured on the
same target cases before declaring it a winner. The tests below check
wiring and safety contracts, not psychological response or real conversion.

## Next steps

1. Keep profile setup and exact CTA verification in the conversion
   path independently of message drafting. Test changes to profile
   treatment separately from voice experiments.
2. Offline paired A/B against saved verified posts, including groups vs.
   channel comments and non-adult products; one model call per candidate
   configuration and post, no Telegram sends.
3. Wire AutoResearch's `voice_variant_id` champion to controlled execution
   and attribution after evidence; evaluate real signups, activation and
   paid CAC with appropriate sample size, not clicks alone.

## Rollback

Revert the feature PR. Both old preview prompt and current account publishing
permissions are unchanged by this implementation.

## Follow-up architectural correction (2026-10-28)

PR #573 introduced a per-draft read of the already connected Telegram profile.
That made the current bio a creative style input and introduced avoidable
Telegram I/O per draft. This was removed in the follow-up change: profile
information is for conversion path verification and profile treatment
optimization, while communication voice is derived from product and audience.
The active creative prompt is `community_message_v16_2_product_led_voice`.
See `docs/TELEGRAM_CONNECTED_ACCOUNT_DRAFT_VOICE_2026-10-27.md`.
