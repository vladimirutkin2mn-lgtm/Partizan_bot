# FemDom Telegram end-to-end read-only acceptance: saved communities

**24 October 2026.** [One-shot research run](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/actions/runs/40098710720) (successful, restricted artifact `femdom-20-peer-readonly-PR545`). Candidate changes in [PR #545](https://github.com/vladimirutkin2mn-lgtm/Partizan_bot/pull/545) are **unmerged and undeployed**.

## Executive findings

The user's requested 20-community cohort cannot be constructed from the **currently accessible saved operational inventory**: there are **9 unique public handles** in the canonical registry and earlier distribution audience map combined (9 registry records, 10 historical map rows with duplicates). The experiment checked the **one actual latest native Telegram message** for each of those 9 peers; no global discovery search, no join/send, no registry writes and **zero LLM calls**.

| Stage | Observed |
| --- | ---: |
| Unique saved public communities | 9 |
| Latest-message native inspections successful | 9 |
| Persistently audience-confirmed | 2 |
| Unconfirmed audience | 7 |
| Confirmed with accessible comment/reply surface on latest post | 0 |
| Post drafts actually generated | 0 |
| Messages sent | 0 |

**Reasons:** both confirmed audiences lack a usable reply surface for the newest channel post: `fem_tut` (latest stored public channel message is from September 22) and `bizarre_afisha` (most recent October 18 media-only, no text). Other seven communities are currently unconfirmed in the actual saved fit records. The generator was **correctly not called** on these nine under the existing upstream gate.

The seven unconfirmed entries are `znakomstvachannel`, `poiskfanfikov`, `play_hard_community`, `findom_gospoja0`, `a_sfera`, `moscowellebdsm1`, `poiskrabovirabin`. Their old *historical relevance scores* are not evidence of confirmed product-audience fit.

The user-selected event `https://t.me/a_sfera/2117` is particularly informative: the read-only native query returned 1,542 real characters of the current post, now enough to go beyond the older **1,200-character** snapshot. Its saved community fit, however, is still marked **unconfirmed**, so the funnel drops it *upstream of copy generation*. This is an **audience confirmation and eligibility issue**, not an inability to generate a playful text.

## Implemented fix for source clipping

`app/telegram_research.py` previously persisted only `text[:1200]`. It now retains up to 4,096 characters of the observed latest text and stores `source_text_chars` and `text_truncated` when necessary. `app/platform_discovery.py` transmits those two fields with native metadata. `tests/test_telegram_research.py` regresses a source with admission fee appearing after the old cutoff. The existing **1,400-character deterministic model-context selection** still reduces LLM spend, with no second summarizer call.

For this single research run, the full-message collection correction was emulated *inside the disposable read-only Python process* using the underlying native `message` text. This did not modify the deployed server, which still uses old code until a separately approved merge/deploy.

## What was and was not validated

Validated: saved inventory count; one newest post per peer; actual native existence/timestamps/text; channel reply-surface status; upstream fit attrition; no Telegram writes; no LLM charge for unusable targets; upstream clipping bug; corrected clipping in isolated audit.

Not validated: 10–15 reviewable drafts or 3–5 publishable candidates (the current cohort yielded none); model v11 real-world naturalness on a diverse qualified selection; independent account-based write permissions; conversion, clicks or signups. **Never relabel an unconfirmed audience or an unavailable comment surface as READY** to make an experiment pass.

## Required next engineering intervention

1. Recover historical discovery leads from *all* documented research sources (not just the 9 registry handles), deduplicate by public Telegram handle, and preserve the distinct statuses `UNCONFIRMED`, `AUDIENCE_CONFIRMED`, `NEEDS_PREFLIGHT`. The source-controlled old three-post fixture also mentions a community absent from this registry; those posts are expired and must not be used as live opportunities.
2. Diagnose why clearly topical posts such as `a_sfera/2117` are not reaching confirmed audience status. Assess **community** relevance with durable community evidence, not blindly with the newest post or coincidental word overlap; keep semantic judgments cached by peer/product, not changing message ID.
3. Prioritize accounts with accessible `GROUP_REPLY` or `CHANNEL_COMMENT` and fresh text before model generation. Post readability is a separate decision from audience relevance and permission.
4. Only then run a capped one-draft-per-latest-post v11 cohort and show the user texts with exact post links, before any explicit publish approval.

**Experiment safety:** Workflow `.github/workflows/femdom-saved-peers-20-oneoff.yml` was changed to **manual dispatch only** after this completed run. Historical research report contains only public-target diagnostics and is not posting authorization.
