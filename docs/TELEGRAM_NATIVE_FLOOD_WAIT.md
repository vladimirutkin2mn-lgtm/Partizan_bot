# Telegram native search: recover from FloodWait without guessing a delay

The Telethon research transport now raises `TelegramFloodWaitError(seconds)`
instead of dropping the duration or sleeping inside a worker.

The read-only research session is shared across products. A flood-wait event
is therefore stored in `telegram_research_flood_wait` as an immutable,
timestamped restriction. A shorter subsequent wait cannot shorten a longer
one. Expired events are removed when checked.

Both native paths consult the restriction:

- Known-peer refresh: preserves the prior VERIFIED snapshot, marks the
  scheduled batch `DEFERRED_FLOOD_WAIT` without falsely claiming a new
  inspection, and keeps the cursor on the interrupted batch. Generic
  transient errors retain the existing rotation behavior.
- New community discovery: refuses to issue a request while blocked; it
  does not reserve a daily query for the skipped call. After a FloodWait,
  the last query remains `RETRYABLE_FAILED` with a real
  `next_attempt_at` and its query fingerprint is not labelled as
  successfully searched. The daily reservation still prevents retry
  storms.

There is no join, send, story, profile edit, paid web search or sleep.

Validation uses fake connectors and an injected clock. Production use of
the new restriction begins only if a real Telethon FloodWait is received.
It does not change existing permissions or turn on research readiness.
