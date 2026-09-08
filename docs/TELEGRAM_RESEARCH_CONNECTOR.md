# Telegram Research Connector

## Status

Phase: Community Distribution Phase 2

- GitHub issue: #250
- implementation PR: #257 — merged
- foundation dependency: #248 / #249 — complete
- programme tracker: #256
- deployed release: `20aa9433dc7301cf541359535eb22e386a265f7e`

The Phase 2 implementation is merged, tested and deployed. The phase is **not production-complete** yet because the final acceptance item requires a real native Telegram research run using an authorised production research session.

A production verification attempt from draft verification PR #258 / CI #808 reached the live API container on the exact deployed release and confirmed that Telegram research is currently disabled operationally:

```text
TELEGRAM_RESEARCH_PROVIDER=unavailable
TELEGRAM_RESEARCH_PUBLIC_READY=false
API ID configured: false
API hash configured: false
Authorised session configured: false
```

The same live verification confirmed Telegram `PUBLISH` remains false and `CLIENT_OWNED` publishing remains unavailable. No credential values were emitted. This is an operational configuration blocker, not a connector-code failure.

## Boundary

Telegram research is a separate, read-only capability from Telegram publishing.

```text
public search evidence
        +
authorised Telegram research session
        ↓
community discovery / inspection
        ↓
DistributionOpportunity

Telegram publishing
        ↓
SEPARATE adapter + publisher mode + permission/readiness gates
```

The research module intentionally exposes no send, join, invite, edit or delete operation. Setting up research credentials must never make `PUBLISH`, `CLIENT_OWNED`, or autonomous execution ready.

## Runtime configuration

Native Telegram research is fail-closed by default:

```text
TELEGRAM_RESEARCH_PROVIDER=unavailable
TELEGRAM_RESEARCH_PUBLIC_READY=false
```

To enable the Telethon transport operationally, deployment secrets must provide:

```text
TELEGRAM_RESEARCH_PROVIDER=telethon
TELEGRAM_RESEARCH_PUBLIC_READY=true
TELEGRAM_RESEARCH_API_ID=<secret/config>
TELEGRAM_RESEARCH_API_HASH=<secret>
TELEGRAM_RESEARCH_SESSION=<authorised StringSession secret>
```

`API_HASH` and the authorised session are `SecretStr` settings. They are not persisted into Distribution Opportunities, customer workspace payloads, logs, or browser JavaScript. The session is constructed in memory for the bounded research call and disconnected afterwards. Session creation/rotation is an operator deployment operation, not a customer publishing connection.

`TELEGRAM_RESEARCH_PUBLIC_READY=true` is an explicit operational switch. Credentials alone are not interpreted as production readiness.

## Bounded transport

The production transport uses Telethon 1.x and only read operations:

- `contacts.SearchRequest` to find public channels/groups from a concise ICP-derived query;
- `get_entity` for a small number of public handles already found by external evidence;
- `GetFullChannelRequest` for public community metadata;
- `get_messages` for a small recent-context window.

The connector clamps each call to small limits. Defaults:

```text
native search results: 5
known handles resolved per request: 3
recent messages read per community: 4
```

Hard connector caps prevent configuration from expanding a single request beyond 10 native results, 5 known handles, or 8 recent messages.

The connector does not enumerate participants, scrape user graphs, send DMs, join communities, invite users, or attempt enforcement avoidance. Telegram/provider rate-limit errors are surfaced as native-research failures; generic public web evidence may remain available, but Partizan does not fabricate native verification.

## Opportunity model

The persistent opportunity is the Telegram **community**, not an individual message.

A natively verified opportunity records:

- stable Telegram entity ID;
- public username and canonical `https://t.me/<username>` URL;
- actual surface kind: `CHANNEL` or `GROUP`;
- title and public about text;
- member count when Telegram exposes it;
- `source_checked_at`;
- `last_activity_at` from observed recent messages;
- recent public message context;
- observational surface capabilities;
- a specific action-target URL only when recent context actually overlaps the research query.

Native canonical keys use the Telegram entity ID:

```text
telegram:<entity_id>
```

This removes the earlier ambiguity where the same public handle could appear once as a guessed `CHANNEL` and again as a guessed `GROUP` from web search.

## Surface capability semantics

Research records whether a *surface* appears to exist:

```text
comment: AVAILABLE | UNAVAILABLE | UNKNOWN
reply: AVAILABLE | UNAVAILABLE | UNKNOWN
standalone_post: AVAILABLE | UNAVAILABLE | UNKNOWN
publisher_permission_verified: false
```

These values do **not** mean a customer or Partizan identity has permission to publish there. Publisher permission is checked later by the separate Phase 3 execution path.

For a broadcast channel, comments are `AVAILABLE` only when Telegram exposes a linked discussion. For a public megagroup, reply and standalone-post surfaces are observable as available, while actual publisher-account rights remain unverified.

## Action target honesty

A community home page is not automatically presented as a specific discussion.

The connector reads a small recent message window and compares observed message text with the ICP-derived research query. It records `action_target_url` only when there is observed term overlap, the message is no older than the 14-day action-target freshness window, and the community exposes the relevant interaction surface. Otherwise:

```text
action_target_url = null
action_target_specific = false
```

This preserves the product distinction between:

1. destination/navigation;
2. exact manual task;
3. founder completion acknowledgement;
4. measurement.

Phase 2 supplies trustworthy target evidence. The later action-execution UX must not invent a specific thread or suggested reply when this evidence is absent.

## Evidence and freshness

Native evidence is tagged:

```text
evidence_type = telegram_native
telegram_entity_id = ...
source_checked_at = ...
last_activity_at = ...
```

Search-query text is provenance only and continues not to count as observed evidence in Audience Intelligence scoring. Native evidence is built from public community title/about/recent message text.

`source_checked_at` means when Partizan inspected Telegram. `last_activity_at` means the latest public message timestamp observed in the bounded context window. The two are intentionally distinct; Partizan does not invent activity freshness from the inspection time.

## Failure behaviour

If native Telegram research is disabled, existing generic public discovery remains available.

If native research is enabled but its session/provider call fails:

- the failure is recorded in `AudienceIntelligenceEngine.last_failures` with `native_enrichment` provenance;
- existing web evidence can still produce a research-only opportunity;
- `native_research_status` remains `NOT_CHECKED` for that fallback opportunity;
- no Telegram native fields are fabricated;
- publishing stays fail-closed.

## Acceptance mapping for #250

| Acceptance criterion | Code / evidence | Current status |
|---|---|---|
| Authorised Telegram research transport + secure lifecycle | `app/telegram_research.py`, `SecretStr` deployment settings, explicit readiness switch | Merged and deployed |
| Discover public channels/groups from ProductProfile + ICP | Telegram adapter topics + Telethon public search | Merged and tested |
| Community is persistent opportunity | entity-level snapshot and `telegram:<entity_id>` canonical key | Merged and tested |
| Source/evidence/freshness/capability metadata | native metadata + recent context + surface capabilities | Merged and tested |
| Concrete communities and actionable URLs | canonical community URL + evidence/freshness-gated `action_target_url` | Merged and tested |
| No deep per-user intent graph | bounded community/message context only | Enforced by architecture and tests |
| Rate/frequency/abuse guardrails | hard per-call caps; no participants/DM/join/invite methods | Merged and tested |
| Research credentials never enable publishing | separate customer capability regression tests | Merged; production fail-closed state also verified |
| Integration tests for discovery/dedupe/freshness/fallback/no-secret leakage/fail-closed readiness | `tests/test_telegram_research.py`, `tests/test_telegram_research_safety.py`, customer channel tests | Green in merged CI |
| Exact release deployed | production deploy #548 + public `/version` | Verified: `20aa9433dc7301cf541359535eb22e386a265f7e` |
| Real production research run | authorised production session + real public community evidence | **OPEN — production research credentials are not configured** |

## Production verification required before closing #250

Production must first be configured with an authorised, research-only Telegram user session. Then run one real product/ICP research request against the exact deployed release and capture non-secret evidence proving:

- exact deployed release SHA;
- native Telegram research was operationally enabled;
- at least one real public community was returned or inspected;
- returned opportunity has public source URL, Telegram entity ID, `source_checked_at`, and observed freshness/context metadata;
- no session/API secret appears in response/log evidence;
- Telegram `PUBLISH` remains false unless the separate Phase 3 path has independently been completed.

Only then may #250 be closed and the Phase 2 checkbox in `COMMUNITY_DISTRIBUTION_EXECUTION_PLAN.md` be marked complete.
