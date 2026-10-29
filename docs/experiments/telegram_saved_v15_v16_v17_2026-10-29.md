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

## First real product-derived voice saved (2026-10-30)

The separate v2 generation run `40947830031` completed successfully.
One OpenAI provider request created and activated voice version
`768f2d13-576b-4311-ab50-629b8ba6e2b6`
(content hash `b700bb763fd6343eedb7b5f302aa42bd0ffe28c6ba38986be19b99116e3546b7`).
Recorded API usage: **791 input tokens, 858 output tokens** including
316 reasoning tokens; **one** provider response, 0 web calls. The system
reserved **US$0.25 estimated**, but the exact billed amount was not
available from the provider response. The original failed v1 job remains
in history; no retries or Telegram sends occurred.

Human editorial review of the saved voice, before any message-generation
test, identified an important quality concern: it strongly emphasizes
rules, moderation, boundaries and formal restraint; its two synthetic
examples resemble moderator corrections instead of organic, playful,
topic-specific community participation. That is a **qualitative concern,
not a proven result on live message examples**. Save the voice as the
unproven baseline and do not infer effective marketing conversion or
auto-promote it. Do not post the raw product/character to repository docs.

The separate `telegram-v17-three-arm-readonly-once.yml` workflow
confirms that the saved version and up to 3 verified old native posts can
be assembled as **one frozen offline request manifest** (0 paid model
calls, 0 Telegram interactions). Historical v15.1/v16.2 arms remain
controlled approximations. A paid three-arm quality test requires a
separate approval and bound, and generated texts require blind review.

## Production image compatibility (2026-10-30)

The first no-spend three-arm manifest workflow (GitHub run
`40949168324`) reached the matching production API release but failed
before generating a manifest: `ModuleNotFoundError: No module named
'tools'`. Docker deliberately copies and installs `app/` but excludes
`tools/`. This failure made **zero paid requests and zero Telegram calls**.

To avoid changing the production image's contents, the research-only
script now includes its own saved Telegram case selector instead of
importing another `tools/` module. The workflow transmits the exact
checked-out script as standard input to `python - --max-cases 3`,
matching the existing v15/v16 research pattern. The paid draft
workflow must use the same stdin mechanism, and remains a separate
unmerged PR until a **successful** zero-cost production preflight.
