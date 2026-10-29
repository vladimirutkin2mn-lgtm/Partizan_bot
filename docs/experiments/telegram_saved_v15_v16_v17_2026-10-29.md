# Controlled offline Telegram voice benchmark: v15.1 / v16.2 / v17

**State:** implementation-only in draft PR #577. No real model results collected, no publication and no enabling of the v17 writer in production.

## Research question

Does a durable product-generated speaking character (v17) lead to a more natural, specific, recognisable community contribution than historical v15.1 and one-call adaptive v16.2? This is NOT a conversion A/B test and cannot demonstrate CAC lift.

## Frozen inputs and limitations

- Read only previously VERIFIED native Telegram snapshots from the existing registry for the known FemDom project. No new Telegram searches, history reads, joins, sends, profile reads, or production writes.
- Use at most three saved cases, across channel comments, group replies and standalone group posts when available. Each arm sees the same saved target, product facts and community information.
- v17 requires an **active saved voice version** whose source fingerprint matches the confirmed product. If missing or stale, stop with ZERO paid requests. Do not fabricate a voice or implicitly generate it.
- The v17 system and user messages are captured from the actual DistributionActionComposer using a fake provider, the real read-only marketing memory, and an in-memory-only draft cache. No DistributionAction or production runtime record is written.
- v15.1 and v16.2 retain their existing guidance but use a normalized current JSON context and DistributionContentDraft output schema. These historical arms are CONTROLLED APPROXIMATIONS, not production replays or a single-variable causal experiment.
- The three-arm comparison lives in tools/compare_saved_telegram_three_voices.py; all exact request hashes are retained.

## Manual command and cost controls

Dry-run by default (ZERO OpenAI calls):

    python -m tools.compare_saved_telegram_three_voices

Manual paid mode is possible ONLY with existing validated saved v17 voice and separate operator approval:

    python -m tools.compare_saved_telegram_three_voices \
      --execute-paid \
      --ack ALLOW_NINE_PAID_DRAFT_CALLS_NO_TELEGRAM \
      --budget-usd OPERATOR_APPROVED_POSITIVE_ENVELOPE \
      --reservation-usd OPERATOR_ESTIMATED_POSITIVE_COST_PER_CALL

The final two all-caps values are placeholders, NOT suggested dollar amounts. The operator must obtain a real model/provider price estimate. Both amounts must be finite, positive, and the entire maximum reservation must fit under the envelope BEFORE the first call. This is an estimated operating envelope, NOT a guaranteed invoice cap.

- At most NINE LLM calls (3 cases x 3 arms), one draft per case and arm.
- At most 900 output tokens per call. Automatic OpenAI SDK retries are disabled.
- Any provider error or unknown outcome stops the run. No fallback or automatic rewriting.
- No CI, deploy, cron or GitHub Action invokes the paid script. No API key committed.
- The result includes draft texts for private human evaluation; no Telegram API invocation or publishing.
- Actual provider invoice amount remains unknown; never infer it from cost reservations.

## Blind quality evaluation

For each complete three-arm case, hide arm labels. Human reviewers then independently score each message (0–4) for: specificity to the exact post, naturalness, recognizable voice, value without profile click, lack of templates, and native-language register. Mark hard vetoes for fabricated personal stories, unsupported claims, policy failures, coercion, forbidden ads, or direct profile CTA where prohibited.

After completing all reviews, reveal labels and compare failures, disagreements, token/cost estimates and qualitative benefits. A three-case pilot does NOT establish statistical confidence or justify auto-promotion. Product, voice or target changes require a new frozen experiment.

## Next step

Build bounded, LLM-generated challenger voice versions with immutable provenance and a manual evaluation bridge. Add generation-run/version linkage through delivered Telegram events and downstream outcomes; only after evidence and a rollback plan consider canary testing. All production feature flags stay OFF.
