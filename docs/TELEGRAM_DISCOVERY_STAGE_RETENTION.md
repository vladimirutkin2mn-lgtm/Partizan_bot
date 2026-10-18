# Retention of Telegram discovery diagnostic checkpoints

The diagnostic queue created in PR #514 writes one row for each of four
discovery stages. Its checkpoints are useful for investigating searches but
are not the authoritative Telegram registry, publisher idempotency history,
paid billing ledger, API quotas or Telegram FloodWait clock.

To prevent indefinite growth of diagnostic rows, each product keeps:

- at most **64 most recent discovery runs**;
- at most **30 days** of old checkpoint history;
- the current latest run **always**, even if it is incomplete or has an
  anomalous timestamp.

During `TelegramDiscoveryStageQueue.start()`, best-effort cleanup is
performed no more than once per UTC day and per product. The maintenance
checkpoint overwrites one fixed per-product record rather than creating
another daily log. Concurrent cleanup is harmless because deletions are
idempotent and use exact product + run + stage keys.

Only rows in `telegram_discovery_stage` are deleted. The following
namespaces are **never** modified by this policy:

- `telegram_community_registry` (verified and unverified communities)
- `telegram_native_replenishment` (query history and cooldown)
- `telegram_research_flood_wait` (Telegram-wide restriction)
- `telegram_paid_discovery_daily_claim`,
  `telegram_paid_discovery_month_slot` (paid query reservations)
- publisher receipts, approvals and Growth AutoResearch experiments.

No extra worker, Telegram session, external web request, paid search or
publication action is triggered. Tests use only a local runtime state
store with backdated synthetic checkpoints.
