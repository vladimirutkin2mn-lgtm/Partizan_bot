# Bounded TGStat public catalog source (opt-in)

This adds the first automatic catalog source to the existing Telegram
registry-first → Telethon native → catalog → paid-web flow. It reuses the
canonical alias-aware `TelegramCommunityRegistry` from PR #508; it does
**not** create another registry, sender or Growth AutoResearch.

## Read path

`TGSTAT_PUBLIC` reads at most **one public ranking HTML page per product
per UTC day**, from the fixed host `tgstat.com`. Pages use only a
conservatively selected topical category, alternating between groups and
channels on later eligible runs. No LLM, API token, login, user roster, or
CAPTCHA bypass is used. Unknown categories are reported and skipped.

The category map is deliberately small: erotica, tech, business, marketing,
education. A missing match never falls back to a generic unrelated ranking.
Page parsing accepts only TGStat `/chat/@handle` and
`/channel/@handle` references and stores a source-bound URL.

The source is **OFF by default**:

```env
TELEGRAM_CATALOG_DISCOVERY_ENABLED=false
```

Changing this to true authorizes read-only public category requests and
**does not** authorize Telegram publication, Telegram account mutation,
or paid web search. Ensure permitted usage and access are reviewed before
enabling the source in production.

## Fail closed and restore

A request is reserved atomically in `telegram_tgstat_catalog_daily_claim`;
status/progress is persisted in `telegram_tgstat_catalog_run`:

- source, category, group/channel rotation, attempt count, last attempt and
  next allowed attempt;
- status `RUNNING`, `STAGED_UNVERIFIED`, `SOURCE_THROTTLED`,
  `FORMAT_UNKNOWN`, `SOURCE_UNAVAILABLE`, `RETRYABLE_FAILED`,
  `NO_VALID_COMMUNITIES`, or `NOT_DUE`;
- number of catalog requests and staged candidate handles.

The next run respects the 24-hour cooldown and a longer numeric
Retry-After when provided. A 403, HTTP 429, changed markup, non-HTML
response or empty/invalid candidate page is NOT a successful catalog
lookup. There is no silent paid-web fallback after such failures.
The source does not sleep or block other projects.

A staged candidate is `NOT_CHECKED` and `RESEARCH_ONLY`, never
`VERIFIED` or `READY`. The existing batched Telethon known-peer
inspection must verify community identity and audience fit in a subsequent
cycle before the candidate is considered qualified; publishability still
requires publisher-account-specific preflight.

## Explicit limitations

- This is **one category-page source**, not a general keyword or geographic
  search; it may return no suitable community or fail against TGStat markup.
  Such limitations are recorded rather than papered over.
- No tested live TGStat/TGStat API response is claimed; tests use an offline
  HTML fixture and fake transports. No scraping or paid search was run while
  implementing this PR.
- No TGStat API Stat subscription/token is used (its `channels/search`
  requires a subscription). If enabled and network access is unavailable,
  the source records a retryable failure rather than pretending success.
- Cost controls are per logical HTML request, not a verified dollar
  charge. Source operator costs, attribution and finer-grained queue
  states are future stages of the full requirement.
- This PR does not auto-enable existing AUTO channels or FemDom publishing.
  DEBUG_REVIEW remains under separate owner approval.
