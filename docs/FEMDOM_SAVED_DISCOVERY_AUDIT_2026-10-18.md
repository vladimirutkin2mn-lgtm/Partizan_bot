# FemDom: historical saved-opportunity check (18 October 2026)

## What this check actually used

The source is `ops/femdom/live_opportunities_2026-10-08.json`,
saved on 8 October 2026. It has three saved Telegram post
opportunities from two public communities. It contains relevance scores,
draft text, saved expiration deadlines, and `NEEDS_REVIEW` states. It does
**not** contain the original search query history, live Telegram access or
conversion results. Therefore this replay cannot say which live keyword
search found a particular post, or that the post is still available.

Run reproducibly against the saved fixture:

```bash
python -m app.saved_discovery_audit \
  ops/femdom/live_opportunities_2026-10-08.json \
  --as-of 2026-10-18T12:00:00+00:00
```

The audit is offline: it uses the same timestamp-derived freshness
classification as `CustomerLiveOpportunityService`, without connecting to
Telegram, submitting a paid search or writing to the operational store.

## Findings at the specified audit time

| Saved target | Stored expiry (UTC) | Freshness | Status |
| --- | --- | --- | --- |
| Bizarre / Real Femdom event, post 794 | 11 Oct 20:59:59 | STALE | Expired, preflight not ready |
| Free People / Kink & Fetish event, post 523 | 13 Oct 20:59:59 | STALE | Expired, preflight not ready |
| Bizarre / Stockings event, post 800 | 16 Oct 20:59:59 | STALE | Expired, preflight not ready |

Summary: **three saved posts, two distinct public Telegram communities, zero
currently publishable targets**, all past their *stored* expiration time.
Historical relevance estimates (0.96, 0.89 and 0.79) must not be confused with
actionability or proven customer conversions.

The audit deliberately never marks a saved post READY for publication, even
if a saved `publishability` field claims READY and its deadline is in the
future. Before any new action, Partizan needs a fresh Telegram verification
of the specific post, an account-specific permission check, and the
appropriate AUTO/DEBUG_REVIEW approval.

## Remaining gaps

- **Original discovery queries and filtering trace:** not present in this
  saved file. Use a future `app.manual_discovery_trace` artifact or the
  new four-stage progress queue with actual completed runs, not inference.
- **Live verification:** not carried out; current availability of the posts
  is unknown.
- **ROI and attribution:** these posts did not supply measured clicks,
  signups or revenue. No conversion success is claimed.
- **Profile optimization / Growth AutoResearch:** needs separate proposals
  and measured feedback; this historical replay alone cannot establish
  a winning conversion treatment.

Do not auto-post the saved suggested comments or resurrect historical
opportunities merely because their relevance score remains high.
