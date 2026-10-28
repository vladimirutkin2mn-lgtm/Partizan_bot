# Offline benchmark of Telegram v15.1 vs v16.2, 2026-10-28

Goal: inspect the actual output quality of the new production product-led
communication architecture, using only **already saved verified native Telegram
post snapshots**, not discovery or a new Telegram session.

- **Baseline**: `COMMUNITY_MESSAGE_GUIDANCE` from v15.1, in its original
  review-only configuration and a neutral structured-output wrapper.
- **Challenger**: current `ACTION_DRAFT_SYSTEM_PROMPT` +
  `COMMUNITY_CREATIVE_GUIDANCE` v16.2 + same neutral wrapper. This
  intentionally compares actual two prompt architectures; it is **not**
  an isolated single-variable experiment.
- **Common inputs**: same expanded product information, same target text,
  community, inferred `adaptive` voice metadata; same model
  `gpt-6-luna` and structured schema. No live profile or persona injected.
- **Source**: existing `telegram_community_registry` product records.
  Choose only `native_research_status=VERIFIED` snapshots and the
  highest positive message ID. Maintain at most one latest saved post per
  allowlisted community. Respect `STANDALONE_POST` as group-root topic,
  not an invented reply. Max six balanced cases, max 12 LLM calls.
- **Zero Telegram actions**: no client, no live API reads, no joins,
  no sends, no profile reads or modifications, no changes to the
  research/marketing runtime store, no audience rescan.
- **Trigger**: one-shot workflow `.github/workflows/telegram-v15-v16-saved-offline-once.yml`
  triggered **only when added to main** (or intentional manual dispatch).
  It runs the script through stdin in the existing deployed API container
  after the matching production SHA is ready, not a second deploy.
- **Results**: paired drafts and identical target snippets in a private
  seven-day GitHub Actions artifact; summary log includes post, comment,
  reasons and per-side usage. It reports missing snapshots and model
  errors rather than substituting invented text.
- **Evaluation**: only complete pairs can be blind-scored for post
  specificity, naturalness, distinct voice, interest in author,
  conversion-path fit, unsupported factual claims, language and safety.
  Require human review of equal inputs and content; no automatic
  promotion. Current Growth AutoResearch evaluation threshold is
  six pairs, 0.3 weighted score lift and no more than 10% extra tokens;
  the initial qualitative inspection does not claim statistically
  significant conversion/CAC impact.
- **Cost control**: one model response for each arm of a selected
  post, max 12 total; no LLM rewriters or graders. The recurring
  production path stays one call per opportunity.
- **Caveat**: the initial v15.1 baseline was tested via a separate
  experimental generator, whereas v16.2 was inserted into the main
  composer. This benchmark uses a shared schema to make drafts
  comparable, not the full production JSON shape. State this clearly.

Follow-up: grade actual draft pairs without looking at arm labels,
analyse which phrases raise curiosity, and decide whether further
voice-variant AutoResearch is justified.
