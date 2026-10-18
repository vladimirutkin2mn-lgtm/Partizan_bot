# Autonomous Telegram discovery: retry scheduling

The existing `AutonomousGrowthWorker` already runs the opportunity refresh
service. This change updates its **existing** schedule instead of adding a
second worker or a competing job queue.

## Provider-independent failures

Previously, a failed discovery did not update the last-success timestamp.
The five-minute worker therefore retried the same failing provider every
tick, even though successful discovery normally ran every six hours.

Failures now persist a per-product `next_attempt_at` and a consecutive
failure count under the existing `autonomous_opportunity_refresh` namespace.
Backoff begins at **15 minutes**, then 30, 60, 120, 240 and a maximum of
360 minutes. The worker skips refresh until this UTC timestamp, including
after process restarts. Its ordinary execution sweep continues unaffected;
previously generated Distribution Plays remain available.

A successful refresh resets the counter to zero and sets the normal
configured discovery interval, which defaults to six hours. Paused mandates
remain ineligible; this feature does not resume paused customer accounts.

## When all research sources are waiting

The refresh service also reads the already-persisted four-stage Telegram
checkpoint for the current run. It can choose the **earliest actual
`next_attempt_at`** from native Telethon/Telegram FloodWait or the permitted
catalog source when *all* of the following are true:

- The current discovery and Distribution Play generation succeeded but
  returned **no opportunities, no READY plays and no active preflight checks**.
- The registry has no inspectable communities (or inspection is rate limited).
- Native discovery was deferred for cooldown or FloodWait and records the
  actual future due time.
- The catalog is disabled, has no matching category, or has its own future
  cooldown; it did **not** just stage unverified communities.
- Paid fallback is disabled/blocked/budget-exhausted, not currently actionable.
- The saved checkpoint belongs to this refresh invocation, not a stale or
  crashed run.

In that narrow case the next refresh can align to the earliest allowed
source time, with a minimum 15-minute spacing. If the checkpoint is missing
or incomplete, the normal six-hour interval remains authoritative.

## Important boundaries

- This scheduling layer does not bypass Telethon FloodWait, Telegram access,
  the catalog rate limit, daily or monthly paid-search budget, publisher
  preflight, the customer Growth Mandate or DEBUG_REVIEW approvals.
- There is no extra Telegram API request, periodic third-party scraper or
  standalone scheduler. Existing worker cadence remains five minutes by
  default and checks persistent next-due timestamps.
- A failed provider response retains its previous portfolio and stores only
  the error class; provider message text and credentials are not persisted.
- Full billing reconciliation and product-facing diagnostics remain separate.
