# Customer Activity: Telegram discovery progress (read only)

The Activity tab now shows a concise **How Partizan is finding opportunities**
panel immediately before the existing live-opportunity and research-community
panels. It is deliberately not a second dashboard, not a backend operator
console and not an action/approval control.

## What a customer can see

- Four source steps: saved communities → native Telegram search → public
  catalog → optional web research.
- Whether the step completed, was unnecessary, was paused by cooldown or
  provider limits, found no relevant result, or staged unverified candidates.
- A short explanation of the source/constraint, the observed community record
  count and the last search phrase when one is actually recorded.
- Last recorded run, source-specific retry windows when known, and the next
  scheduled opportunity-refresh check if the existing worker has one.

These are **research diagnostics**. Seeing `COMPLETED`, a search term or a
candidate count never means permission to join, send, reply, change a profile
or publish. Publisher access is still checked separately; the existing
DEBUG_REVIEW / AUTO customer controls are unaffected.

## Data boundaries

`GET /customer/workspace/{project_id}/telegram-discovery` requires the same
authenticated project-ownership check as the existing live-opportunities and
research-communities endpoints. Project ID is resolved to the product ID
server-side; callers cannot inject another product ID.

Only public-facing field names, stage statuses and whitelisted generic reason
texts are returned. The API explicitly excludes raw provider exceptions,
internal Telegram sessions, publisher identities, request tokens, billing
records, internal run keys and action targets. Unknown internal reason text is
replaced with a generic safe message. Search phrases are truncated to 180
characters and escaped when rendered into the customer UI.

There is no write method, automatic retry action, paid-source enablement,
Telegram join, account mutation or publication in this panel.

## No-data states and freshness

A product without a recorded stage queue shows **Not started**, not a
fabricated last search. An interrupted run displays the remaining steps as
**Not checked**. Source errors show a non-sensitive reason. A failed
read-only fetch shows **temporarily unavailable**.

The panel refreshes with the existing Activity refresh loop (60 seconds).
Project switches discard stale responses from the previously selected project.

## Verification

Regression tests cover owner-only API access, unauthorized access and project
isolation, missing mappings and no-run states, safe redaction of internal
failure information, the four-stage payload and frontend read-only rendering.
No live Telegram or paid web research was triggered for these checks.
