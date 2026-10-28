# Persisted voice foundation — implementation 1/6 (2026-10-29)

Status: **development PR, not production rollout**. Source of requirements:
`Partizan_voice_autoresearch_requirements_2026-10-29.md` from the owner's
ChatGPT Library. Target commit reviewed: `f91e3b1...`.

## Implemented in this PR

- `app/voice_profiles.py`: source snapshot / fingerprint, strict
  LLM-generated structured `VoiceProfileDraft`, marked synthetic examples,
  immutable `VoiceProfileVersion`, durable `VoiceGenerationJob`, assignment
  and revision-controlled promotion. Scope is exact project/product. Existing
  projects currently do not have a first-class tenant_id; the project UUID
  is recorded as an interim isolation scope. No auth route exposes these APIs.
- `RuntimeStateStore.compare_and_swap`: atomic in-memory and PostgreSQL
  conditional update to prevent concurrent assignment overwrite and duplicate
  reserved-job execution; `put_if_absent` provides durable claim/reservation.
- `VoiceProfileService.reserve_initial` creates an idempotent **unpaid**
  job. `generate_reserved` uses a real `LLMProvider` interface and
  explicit `allow_llm_call=True` only. No background scheduler or HTTP
  endpoint invokes this operation. The caller must separately enforce an
  explicit research/spend mandate before making a paid call; such a budget
  execution bridge is **not implemented** in this PR.
- `app/persisted_voice_writer.py`: versioned v17 writer instructions use a
  saved, concrete character from the correct project/product scope. It
  cannot use current Telegram bio/username and it does not generate the
  character per message. The exact `voice_profile_version_id` and style
  hash enter the writer input. This is not yet persisted to DistributionAction.
- `app/action_drafting.py`: opt-in `project_id` and
  `voice_version_id` support for the existing Telegram compose path,
  under `PERSISTED_VOICE_WRITER_ENABLED=false` by default. If enabled and
  a project id or valid voice is missing, the writer fails **before** calling
  the LLM. The current live prep routes do not pass project IDs; DO NOT enable
  the flag yet or those prep routes will fail closed.
- Fake-provider tests exercise no automatic LLM call, bounded scoped
  generation, durable re-use, invalidation on product change, no cross-project
  reuse, writer one-call behaviour and atomic promotion revision.
- No Telegram network, posting, joining, profile mutation, paid LLM execution,
  migrations or worker jobs are triggered.

## Known gaps (intentionally not marked done)

1. The user has requested **automatic** initial generation after product
   understanding; this PR does not wire the job into intake or existing
   worker. Next step needs budget reservation and a dedicated worker
   without hidden paid calls on merge/deploy.
2. Current project scope has no explicit tenant identity in persistence.
   Project IDs and product matching provide initial isolation; add an
   authenticated multi-tenant boundary before exposing a read API.
3. No durable worker lease/recovery, request manifest/usage cost registry,
   or explicit handling of partial remote success beyond
   `PROVIDER_OUTCOME_UNKNOWN`. Failed unknown jobs must not be silently
   retried or charged again.
4. `VoiceProfileVersion` is immutable by service convention and
   `put_if_absent`; a raw store writer still has `put`. Harden database
   invariants/permissions in a dedicated persistence migration before
   multi-tenant production rollout.
5. The new writer uses the existing `DistributionContentDraft` schema but
   preview/production do not yet share a common request builder, and
   `voice_profile_version_id` is not yet recorded in the action, trial,
   or downstream outcomes.
6. No AutoResearch challenger, version routing through execution bridge,
   evidence evaluation, or safe automatic promotion/rollback yet.
7. No feature flag for separate initial-generation, live canary or
   promotion; must be added before any automated activation.
8. No real LLM-generated sample/test on saved Telegram posts yet.
   Synthetic tests verify **wiring only**.

## Rollout/rollback

Never enable `PERSISTED_VOICE_WRITER_ENABLED` in production until project
routing, init jobs and conversion contracts are completed and verified.
Default is false; legacy v16.2 remains unchanged. This PR may be reviewed
and merged only when code/tests pass; merging is **not** permission to
run generation, write to Telegram, modify profiles, or enable flags.

## Next PR sequence

- Phase 2: initial generation worker with explicit budget/authorization,
  verified product source, durable lease/usage/errors and tenant isolation.
- Phase 3: single canonical writer request builder reused by preview and
  execution, explicit project scope + verified conversion brief and
  immutable action provenance.
- Phase 4-6: versioned research candidates, real execution-bridge routing,
  downstream attribution, evaluation, controlled canary and rollback.
