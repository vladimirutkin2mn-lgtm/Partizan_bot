# Telegram: adaptive voice + cheap long-post excerpts (v11)

**Date:** 2026-10-24. **PR:** #545, unmerged candidate.
**Decision:** User liked both the v9 playful contrast and the v10 teasing style, and asked whether long posts need to be read in full.

## Message generation and tone

The new `community_message_v11_adaptive_playful` keeps the one-call/one-draft contract and combines the two useful response styles rather than requiring a role-related joke in every post. For relevant playful adult communities, the draft can be coy, ironic and attentive to subculture vocabulary, but should choose a genuinely supported angle in the exact target post. It must not fabricate attendance, personal experiences, event rules or concealed product endorsements. The final delivery/authorization layer still controls publication; these changes do not post to Telegram.

## Zero-LLM target text budget

New module: `app/telegram_target_excerpt.py`, called by both `app/action_drafting.py` and the bounded `app/femdom_research_comment_preview.py`.

- Up to **1,400 characters of available selected post**: include unchanged.
- Above 1,400: include opening (up to 500 chars), ending (up to 300), and up to roughly 450 characters of high-signal middle text, including prices, dates/numbers, participation/registration rules, capacity, program, dress code, auction and payment indicators.
- Add explicit omission markers so the LLM cannot assume those are the whole post; include source-length and compaction metadata in the structured prompt.
- **Zero additional model calls**: no LLM summarization, no reading earlier posts, no automatic re-search. The same one existing generation call processes the selected text.
- Strong regression tests: the start, **4311 fee in the middle**, final registration conditions and hard 1,400-character bound are preserved in an illustrative synthetic long post; short messages are unchanged. An integration test checks the actual production model request.

The earlier composer limit was a flat **first 2,400 characters**. That could drop late details and cost more per model call. New maximum is 1,400 for post text; potential post-text reduction at maximum is `(2400-1400)/2400 = 41.7%`, **not** a promise to save 41.7% of the whole API bill, since system instructions and response tokens are separate. For posts already below the limit, no reduction.

### Known source truncation

Historic `a_sfera/2117` snapshot stored **1,200 characters**, stopping mid-post. That is a limitation of the **saved upstream source**, not the generation truncation. The new selection does not restore absent text; if the contribution fee or later program notes were never saved, the LLM cannot know them. It is safer to keep one latest native post faithfully and fix the collection/storage cap in a later targeted ingestion change once its source is identified than to add a separate paid summarizer.

## Validation and rollout limits

The model prompt and output schema remain English instructions, one reply in the community language. The number of generation calls is unchanged. Unit/integration tests validate byte/text selection and metadata; they do not establish better actual Telegram engagement. Candidate v11 has **not** been deployed and the prior v8-v10 one-shot review workflows remain manual-only. No new paid live-model A/B run was necessary for the mechanical excerpt change.
