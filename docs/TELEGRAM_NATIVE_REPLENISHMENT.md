# Telegram registry replenishment: native-first under sparse coverage

This is the first incremental slice after PR #505, isolated from actual Telegram
publication, profile changes, and any paid web discovery.

## Trigger and source priority

For Telegram products with **zero or more** saved communities, the autonomous
CommunityFirstAudienceIntelligenceEngine:

1. rotates up to three known public handles through the existing read-only
   Telethon known-peer inspector;
2. counts only communities that have confirmed audience fit, a VERIFIED native
   snapshot and no JOIN_REQUIRED restriction;
3. when the count is below **three**, attempts **one** native public search query
   through the configured Telegram research session;
4. keeps adaptive searches with new hints on the same saved-registry path,
   rather than letting an adaptive round invoke paid web search;
5. runs the existing audience-fit check and upserts accepted discoveries through
   TelegramCommunityRegistry (entity ID / handle aliases are deduplicated);
6. preserves the saved communities even when native expansion fails or returns
   nothing.

No paid web search is issued for this saved-registry path. The new path does not
join communities, publish content, modify profiles or initiate dialogs.

## Budget, retry, diagnostics

- Up to **one native search per product per UTC day**, with an additional
  rolling **24-hour cooldown**.
- Search topics are drawn from at most three ICPs and their existing bounded
  community-first request plans; a normalized SHA-256 query key prevents
  repeating the same query for **seven days**.
- A per-product/day claim in RuntimeStateStore prevents concurrent workers from
  firing duplicate native searches. A three-day-old claim is removed during
  each successful reservation.
- Search state is under `telegram_native_replenishment`. It records the
  attempted query, status (STARTED, FOUND, NO_FIT_MATCH, RETRYABLE_FAILED),
  candidate count, error type (not provider secrets), last attempt timestamp
  and recent query hashes.
- A native API error leaves historic VERIFIED snapshots intact. There is **no
  automatic paid fallback** from the saved-registry native search path.

## Intentional limits of this PR

- **Empty registries now bootstrap with native Telegram search** using the
  same product-level native cooldown, query history and canonical registry.
  A successful native candidate skips all Telegram web discovery for that run.
- Paid Telegram web discovery is **off by default**. Operators may explicitly
  set `TELEGRAM_PAID_DISCOVERY_ENABLED=true` for a last-resort fallback when
  native search leaves the registry empty. The OpenAI web provider is
  atomically limited to **one Telegram query per product per UTC day**,
  including provider failures and adaptive rounds; other enabled platform
  searches are not affected.
- The budget is a **query-count guard** rather than a verified dollar cap.
  For a monetary hard cap, provider-side billing/spend controls and explicit
  cost reconciliation are still required.
- Catalog/parsers (tgscan, telegram-groups-parser, Telegram-Chat-Scraper) are
  not run by this PR. They must be reviewed for safety, ToS, licensing,
  resilience and canonical deduplication before integration.
- The threshold of three is a provisional coverage gate, **not** evidence of
  three actually executable publication opportunities. Execution preflight
  remains independent and stricter.
- FemDom regression tests use synthetic stored communities and a fake native
  connector, not production client credentials or live Telegram traffic.
- No deployment or production acceptance test is performed by these changes.

## Regression tests

`tests/test_telegram_native_registry_replenishment.py` validates sparse,
sufficient and JOIN_REQUIRED coverage; query rotation and cooldown; identity
persistence; and failure without a paid fallback.
