# Telegram discovery: consolidated stage queue and diagnostics

After the canonical registry, native Telethon refresh/replenishment, TGStat
catalog and paid budget gates were developed separately, it became difficult
to tell why a discovery run stopped or when another attempt is possible.

This change adds a **persistent diagnostic stage queue for each execution**,
without replacing existing proven search, budget, rate-limit or publication
guards.

## One run, four ordered stages

Every enabled Telegram discovery run creates four independent checkpoints:

| Stage | Existing authority | Gate |
| --- | --- | --- |
| `registry` | Canonical Telegram registry / known-peer inspection | Public handles only, native verification |
| `native` | Saved native search state and Telegram-wide FloodWait | Native fit, 24h cooldown, seven-day query cache |
| `catalog` | Opted-in bounded public TGStat reader | Catalog identities remain unverified |
| `paid` | Completed-native fallback gate + daily/monthly reservations | Operator opt-in and configured cost envelope |

A checkpoint stores the product ID, run ID, priority, state, reason, source,
search query (where available), gate/filter, candidate count, and next
allowed attempt when the original source knows it.

Possible outcomes include `COMPLETED`, `NO_FIT`,
`STAGED_UNVERIFIED`, `FAILED_RETRYABLE`, `DEFERRED_COOLDOWN`,
`DEFERRED_RATE_LIMIT`, `BLOCKED`, `BUDGET_EXHAUSTED`, and explicit
skips. A crash leaves unfinished stages `PENDING`, rather than falsely
marking success.

Per-run stage keys are unique, so simultaneous discovery calls cannot
overwrite one another's checkpoints. The `latest` pointer is only for
operator convenience; authoritative state is still held by each provider's
original budget/flood-wait/canonical registry records.

## Read-only operator inspection

With the correct runtime storage configuration, view the most recent run:

```bash
python -m app.telegram_discovery_stage_queue --product-id <PRODUCT_UUID>
```

This command reads stored checkpoints only. It issues no Telegram search,
catalog HTTP request, paid provider call or publishing action.

The engine also exposes `last_telegram_stage_queue` for testing and
later read-only UI integration. No new user-facing control was added in
this change.

## Execution and crash recovery

The existing autonomous growth worker remains responsible for calling
`CommunityFirstAudienceIntelligenceEngine.discover`. At each such call,
the original native cooldown, FloodWait clock, catalog next-attempt timestamp,
one-query/day limit and monthly budget are checked again. The new queue
**records** that progression rather than creating an independent second
scheduler with competing retry rules.

Therefore the checkpoint layer does **not** mean an independent background
queue worker, automatic polling of Telegram or a complete monetary billing
reconciliation. Those require separate work. Historical records currently
remain in RuntimeStateStore; a retention/compaction policy will be needed
for long-running installations.

Tests use fake native and paid providers and synthetic FemDom scenarios.
There is no real Telegram, TGStat, paid web or publication action.
