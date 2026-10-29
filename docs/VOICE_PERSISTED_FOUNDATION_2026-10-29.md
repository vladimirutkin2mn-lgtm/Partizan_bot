# Persisted voice implementation — phase 1 + guarded parts of phases 2/3 (2026-10-29)

**Status:** Draft PR #577, under test, not a live rollout. Based on
`Partizan_voice_autoresearch_requirements_2026-10-29.md`.
Reference main commit: `f91e3b1c17d9b31ab0e90de59ad7b555d429c6f1`.

## Shipped in this branch (not yet active)

1. **Voice records:** `app/voice_profiles.py` defines strict LLM generated
   `VoiceProfileDraft` (concrete style + synthetic example labels), immutable
   `VoiceProfileVersion`, `VoiceAssignment`, `VoiceGenerationJob`, source
   snapshots and content hashes. Isolation is by project/product. In absence
   of a separately established tenant ID, project ID is the interim scope.
2. **Atomic claims:** `RuntimeStateStore.put_if_absent` reserves one initial
   generation job; new `compare_and_swap` claims jobs and changes active
   versions atomically (in-memory lock / PostgreSQL conditional UPDATE).
   Unknown provider outcomes stop rather than retrying hidden paid calls.
3. **Initial onboarding reservation:** `CustomerFunnelService.confirm_preview`
   can reserve an initial voice job for a confirmed product and store
   `voice_initial_job_id`, behind
   `VOICE_INITIAL_RESERVATION_ENABLED=false`. This reservation is **unpaid**,
   does not generate a style synchronously, does not ask the customer for
   their tone, and does not block legacy onboarding when disabled.
4. **Bounded one-shot worker:** `app/voice_initial_worker.py` will consume
   at most one eligible saved job per invocation, only with
   `VOICE_GENERATION_WORKER_ENABLED=true`, a configured paid OpenAI provider,
   explicit positive `VOICE_GENERATION_MONTHLY_BUDGET_USD`,
   `VOICE_GENERATION_CALL_RESERVATION_USD`, and
   `VOICE_GENERATION_MAX_OUTPUT_TOKENS` between 256-4000. It uses atomic
   product/month reservation slots before the call and captures usage to
   the durable job; billed cost remains UNKNOWN. This worker is **not wired
   into cron or deployment**. Both the worker flag and reservations are
   off by default. USD amounts are operator-estimated envelopes, **not**
   externally verified or guaranteed provider invoice caps.
5. **Saved-voice writer:** `app/persisted_voice_writer.py` defines v17
   prompts for a single topic-specific message using a stored concrete
   character. `DistributionActionComposer` resolves a chosen scoped
   version or current assignment only when
   `PERSISTED_VOICE_WRITER_ENABLED=true`; it never reads a Telegram profile
   or generates a new character inside a message. Missing/ambiguous scope,
   missing initial voice, changed product facts or wrong version fail closed
   before sending to the LLM.
6. **Real preparation path:** `auto_prepare` and
   `auto_prepare_variants` pass `project_id` + `voice_version_id`.
   When omitted, the enabled Telegram writer resolves a unique CONFIRMED
   product project or fails; it never guesses between two customers. v17
   adds a PROFILE_CLICK no-link/no-product brief for organic tactics where
   links and product mentions are not part of the template.
7. **Provenance and bridge:** `GrowthVariantSpec.voice_profile_version_id`
   can refer to an actual immutable voice. The existing
   `GrowthAutoResearchExecutionService` forwards the version through
   `auto_prepare`. The resulting `DistributionContentDraft` records the
   executed version+hash; `DistributionExecutionService.prepare` persists
   them in the prepared action's `content_payload`, via **internal-only**
   keyword arguments. The public `DistributionExecutionPrepareRequest`
   cannot spoof these metadata fields.
8. **Testing:** fake provider/store test coverage for idempotent initial
   reservation, explicit paid gating, finite quota, provider timeout/no
   automatic retry, scope isolation, data-change invalidation, writer
   one-call behavior, the existing on-boarding entry, the actual
   `auto_prepare` routing and the AutoResearch execution bridge.
   No live model or Telegram API is invoked by CI tests.

## Not yet complete under the 691-line specification

- No automatically scheduled paid initial worker, full spend ledger/pricing
  reconciliation, provider response snapshot manifest, durable lease
  recovery or authenticated project-level voice inspection route.
- No shared canonical preview/production request builder across *all*
  variants, nor guaranteed message-generation cache/idempotency over
  retries. The legacy v15.1 and v16.2 paths still differ.
- No true LLM-generated AutoResearch challengers, trial-specific voice
  candidate version creation, budgeted blind judge, confidence-calibrated
  evaluation, version assignment to randomized live canary, reliable
  per-arm downstream attribution or automatic promotion/rollback.
- Voice version is recorded on the action, but a separate
  `GenerationRun` ledger and version linkage to *every* outcome event
  have not yet been implemented.
- Existing actions are not backfilled with made-up versions.
- This branch has not shown better actual messaging; synthetic fake
  providers validate only integration correctness.
- This PR does **not** authorize enabling publishing, turning on paid
  research or executing Telegram changes.

## Rollout controls and safe state

All new toggles default to **false**:

- `VOICE_INITIAL_RESERVATION_ENABLED=false`
- `VOICE_GENERATION_WORKER_ENABLED=false`
- `PERSISTED_VOICE_WRITER_ENABLED=false`

No paid test-on-merge/CI/deploy jobs have been added. **Do not flip the writer
flag independently**: product-specific confirmed project and its validated
active voice must exist first, and all product/customer flows must pass.
Rollback: disable writer flag; v16.2 remains the legacy path.

## Follow-up

- Finish full request manifest, provider/usage pricing and durable
  generation/cache and writer idempotency.
- Add a shared preview/production writer request builder, deterministic
  policy/CTA gate and one dry-run on saved cases.
- Implement dedicated voice challengers through the existing
  AutoResearch policy + evaluator with actually attributable outcomes;
  keep canary/promotion separate and opt-in.

## Follow-up slice: paid writer request idempotency (2026-10-29)

- `app/voice_draft_cache.py`: a persistent, scope-bound and atomic v17 draft request reservation. The digest covers the exact system/user prompt (including voice version, selected native target, product, community, policy, memory and conversion brief), response schema key, project and product IDs, plus immutable voice content hash. The reservation record stores only the digest and generated draft, not raw request text.
- Identical successful requests reuse the stored `DistributionContentDraft` and its exact voice provenance without a second paid LLM call. Changing the post or input manifest creates a different reservation.
- Concurrent/in-flight reservations, provider timeouts and cancelled calls are **not retried** by this path. Unknown provider outcomes are recorded as `PROVIDER_OUTCOME_UNKNOWN`. Deterministic missing-disclosure rejection is recorded as `POLICY_REJECTED`. Either state requires separate operator reconciliation rather than automatically billing again.
- v17 never silently substitutes a mock draft when the LLM provider is missing. Feature OFF retains the legacy path.
- Added local fake-provider regression cases for replay, distinct target, unknown provider outcome, disclosure rejection, and missing-provider fail closed. No scheduled paid calls or Telegram actions introduced.
- **Limitations:** this is request-level idempotency within the current composer path, not yet a unified preview/production request builder. A crash after provider success and before cache completion deliberately leaves an unreconciled request. There is not yet a complete generation-run ledger, full billing reconciliation, scheduled worker, true voice AutoResearch challenger evaluation or canary. The feature remains OFF and PR remains draft until CI and full integration checks succeed.

## Follow-up slice: deterministic profile-click validation (2026-10-29)

- v17 now validates generated organic `PROFILE_CLICK` messages **after** the paid model call but **before** caching/preparing an action. Obvious external URLs, direct profile/bio CTAs (Russian and English) and exact nontrivial product-name mentions are rejected.
- This complements the writing instructions; it is intentionally narrow, not a semantic classifier or a guarantee against all promotional wording. It is **not applied** to other conversion mechanisms that allow in-message promotion. Existing mandatory disclosure checks still run independently.
- The rejection is stored as `POLICY_REJECTED` in the draft cache, so an already paid result is not silently rewritten or regenerated with more spending. Operators must review any conflict between community disclosures and the organic conversion mechanism.
- Regression coverage includes links, Russian and English profile invitations, product name, a neutral use of the word «профиль», and the no-rebill integration path. No account actions, paid runs, publisher toggles or deployments were authorized.


## Follow-up slice: identical review and preparation route (2026-10-29)

- `DistributionActionDraftingService._draft_single` is now the **single shared entrypoint** for v17 no-send review and ordinary single-message `auto_prepare`. It verifies READY play, selects exactly the latest approved native Telegram target through the existing selector, checks for already prepared/attempted actions, resolves the scoped persisted voice, and calls the same `DistributionActionComposer` (exact prompts, model schema, project memory, request digest, cached text and post-generation gates).
- `preview_persisted_voice` is an **internal, non-publishing** review function: returns target, one content draft, immutable voice version/hash and prompt version. It does **not** create a DistributionAction, grant send permissions, join groups or issue a Telegram API call. It requires the v17 flag ON and a real/injected LLM provider; there is no automatic paid preview job or new customer API exposed.
- `customer_telegram_preview.run` already calls the standard `auto_prepare_variants` path. For v17, it now pins project + active voice ID, persists the same provenance with the cached human-review record, displays the action's immutable version/hash, and refuses stale cached previews after voice changes or writer disablement. The variant path still sends its explicit conversion brief to the SAME underlying composer; therefore the prompts can differ legitimately from a non-variant request. Historical v15.1 FemDom research previews stay isolated for reproducible comparisons rather than being silently migrated.
- Added fake-model regression for exact preview -> `auto_prepare` response reuse and **zero extra LLM calls**, no action created during review, plus stale customer preview version/disablement checks and rendered provenance.
- **Not done:** no real v17 generated text or v15.1/v16.2/v17 blind comparison has been produced (paid model not called); no full cross-mode output equivalence claim for different conversion variants; no production deployment, live sending, scheduled worker or automatic AutoResearch promotion. All feature flags remain off pending a scoped, budgeted rollout.

## Follow-up slice: read-only three-arm research harness (2026-10-29)

- `tools/compare_saved_telegram_three_voices.py` freezes up to three saved, VERIFIED Telegram snapshots and requires a confirmed, **active** and source-fingerprint-valid persisted v17 character before preparing any model calls.
- For v17, an in-memory-only `DistributionActionComposer` invocation with a fake provider captures the production writer's exact system/user messages. This uses read-only scoped voice and marketing memory data, never the normal persistent draft cache or publication service.
- Historical v15.1/v16.2 guidance is compared with a common normalized context and `DistributionContentDraft` schema. These historical arms are **approximations**, not byte-identical historical production replays. Each side's prompt digest is retained.
- Default CLI is preflight-only, zero paid calls. Manual execution requires independent acknowledgement, positive finite full-call cost envelope, at most nine LLM calls and 900 tokens per request, SDK retries OFF, and a fail-stop on unknown provider outcome. Operator envelope is **not** an invoice guarantee.
- Tests use fake LLM providers to check prompt capture, no production store mutations, group standalone input, preflight rejection, explicit paid gating and error stop. No worker, cron or deployment invokes a paid comparison.
- **Not yet delivered:** actual three-arm generated draft samples or blind human grades. The branch cannot assume production has an initialized v17 voice. No rollout flag was enabled. See `docs/experiments/telegram_saved_v15_v16_v17_2026-10-29.md`.

## Follow-up slice: manually bootstrap a confirmed existing product (2026-10-29)

The old worker could only process previously RESERVED jobs, and processed the oldest eligible job globally. Existing confirmed products created before the reservation feature was enabled had no supported path to initialize their author character. This slice adds an explicit **scope-bound** bootstrap in `app/voice_bootstrap.py`.

Three separate steps (examples use placeholders, not paid price recommendations):

1. **Read-only, zero-cost preflight** — after this branch is available in a properly configured environment:
   `python -m app.voice_bootstrap --project-id PROJECT_UUID --product-id PRODUCT_UUID`
   It verifies project ownership, confirmed understanding, confirmed product intake, product source fingerprint and current assignment/job state. It never reads a Telegram session, changes a writer flag or calls an LLM.
2. **Unpaid durable reservation** — explicit manual action:
   `python -m app.voice_bootstrap --project-id PROJECT_UUID --product-id PRODUCT_UUID --reserve-only`
   Requires the database-backed runtime and is idempotent. It returns one scoped `job_id` to review before authorizing generation.
3. **One exact paid voice generation** — separately approved manual action, only for the reviewed `job_id`:
   `python -m app.voice_bootstrap --project-id PROJECT_UUID --product-id PRODUCT_UUID --generate-paid --expected-job-id REVIEWED_JOB_ID --ack GENERATE_ONE_PAID_INITIAL_VOICE_NO_TELEGRAM --monthly-budget-usd APPROVED_MONTHLY_AMOUNT --reservation-usd APPROVED_PER_CALL_ESTIMATE --max-output-tokens 1600`
   Replace the amounts with an independently verified, finite and positive operator budget. These are **estimated envelopes, not actual billed charges or API guarantees**. No execution is triggered by CI, merge, deployment, cron, or a user channel toggle.

The one-shot paid execution uses the worker's atomic budget/job claims but now supplies an **exact job selector**. The paid provider additionally configures OpenAI SDK `max_retries=0` for this worker only, eliminating hidden automatic retry bills. Other project jobs cannot be selected by the manual command. Conflicting, outdated or partially completed jobs require reconciliation rather than implicit retries.

For human inspection of the generated character **without another call**:
`python -m app.voice_bootstrap --project-id PROJECT_UUID --product-id PRODUCT_UUID --inspect-active`
The output contains the actual persisted character and its immutable version/hash and notes that examples are synthetic. This does NOT enable v17 drafting/publishing.

**State/evidence:** production has not been changed, no live voice model has run here, no real voice or actual v17 texts have been produced. This is an operator-only initial generation bridge, not fully automatic onboarding or a scheduled worker. Before any real three-arm pilot, execute and inspect the actual generated voice, then run the *default free preflight* of `tools/compare_saved_telegram_three_voices.py`; a paid pilot still requires its own approval and envelope.

## Legacy confirmation compatibility after first production read-only preflight (2026-10-29)

The first production preflight on the known FemDom project returned
`VOICE_PRODUCT_UNDERSTANDING_NOT_CONFIRMED` (provider calls = 0). The
original bootstrap had required a field that predates some older confirmed
projects. The existing `CustomerFunnelService.continue_preview_research`
already supports the historical distinction: an **absent** project flag with
a `CONFIRMED` ProductProfile is legacy-confirmed, whereas an explicitly
false confirmation must stay rejected.

The updated **read-only** voice plan now reports
`LEGACY_CONFIRMATION_BACKFILL_REQUIRED` for exactly that legacy case
without modifying project or product. A separately invoked unpaid
`--reserve-only` action performs an atomic compare-and-swap of the
project's confirmation field *only* when the key is absent and the bound
product is confirmed; it then reserves one exact voice job. Explicit
`understanding_confirmed=false` or `null`, draft products, mismatched
project ownership, stale snapshots and conflicting writes remain blocked.

The new one-shot read-only production workflow is re-triggered by its own
changed YAML only once merged into main. Neither that workflow nor the code
change executes the unpaid reservation or any paid provider call.
