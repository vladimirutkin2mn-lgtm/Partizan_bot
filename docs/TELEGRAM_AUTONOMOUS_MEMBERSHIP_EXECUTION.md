# Telegram autonomous discovery → membership → execution

Partizan customers do **not** need a "Check group" step per community.

Customer-facing setup is: choose Telegram, connect client-owned account,
enable bounded automation and optionally enable `allow_community_joins`
(the new authorization UI preselects this scope for clear opt-in). Older
automation grants default to `false` so no existing customer inherits
unconsented account mutations.

## Execution workflow
1. Read-only discovery and native publisher preflight select relevant
   communities/posts. Discovery and refresh NEVER join or send.
2. When an APPROVED Telegram action executes under an enabled AUTO channel
   and active customer-owned automation authorization, preflight re-checks
   current account write access.
3. READY → publish through the unchanged governance and publisher path.
   JOIN_REQUIRED → only if `allow_community_joins` is true, validate
   immutable saved opportunity ID and target URL, native entity ID, fresh
   24-hour evidence, DIRECT-confirmed audience and a sourced community rule
   allowing participation and promotion. A community moderator's ban or
   restriction always wins.
4. At most 3 joins per project/day. Claim a durable membership-attempt key
   by project+native destination ID BEFORE contacting Telegram. No blind
   repeats after errors or process crashes. Native group IDs are rechecked.
5. After one Telegram join, run a fresh read-only post-join preflight. If
   permissions are not READY, STOP. Membership is not send authorization.
6. ONLY the already approved action passes to the existing publisher,
   telemetry, cost recording, result observation and portfolio analytics.
   Failure/uncertainty never incurs a successful-publish charge.

## Source-verification requirement (intentional fail-closed gate)

The execution path needs `community_commercial_rules` metadata:
`{"status":"VERIFIED_ALLOWED","allows_participation":true,
"allows_promotion":true,"source_url":"https://..."}`.
Audience fit or the existence of a writable UI button **cannot** prove that
promotional messages are permitted. Unverified/forbidden rules do not trigger
membership or publishing. Future autonomous community-rule extraction must
supply independently verifiable source evidence and freshness; do not hardcode
`VERIFIED_ALLOWED` on every community or promote user-provided marketing text
as moderator permission.

## Tests and roll-out

- New opt-ins explicitly include self-join; old grants cannot be auto-upgraded.
- Comment under channel linked discussion, reply under a group, and standalone
  group posting support the same conditional JOIN_REQUIRED recovery.
- Not permitted: auto joining on discovery, manual preview, unapproved action,
  existing account ban, ambiguous saved opportunity, stale native snapshot,
  policy unknown, target mismatch, failed/unknown join result or repeated retry.
- The existing FemDom dogfood/manual experiment continues to require explicit
  `публикуй`; this implementation does not turn that test into autonomous mode.
- No changes to account avatars, bio, public profiles, publishing cadence
  beyond existing caps, or paid provider costs.

The legacy per-group membership API can remain an operator diagnostic tool;
it is not a normal customer journey. ChatGPT-connector support is orthogonal
to Partizan's autonomous execution.
