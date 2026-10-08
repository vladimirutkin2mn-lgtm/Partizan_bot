# Telegram opportunity flywheel

Partizan production already runs the autonomous growth worker every five minutes. The flywheel keeps that execution cadence and adds a slower public-opportunity refresh cadence.

## Cadence

- autonomous execution sweep: every 5 minutes by default;
- fresh public opportunity discovery: every 6 hours by default;
- discovery is throttled independently per active Telegram Growth Mandate.

## Flow

1. Reconcile Autopilot safety policy.
2. When discovery is due, rerun Audience Intelligence for active Telegram Auto products.
3. Persist concrete native Telegram message targets into customer live opportunities.
4. Run a read-only publisher preflight for exact Telegram COMMENT targets using the connected customer account.
5. Exclude targets that cannot actually accept a comment from autonomous execution: no discussion, target missing, no write access, or publisher/session preflight failure.
6. Keep `JOIN_REQUIRED` visible as a recoverable opportunity, but do not mark it executable. Joining a linked discussion remains a separate explicitly authorized operation.
7. Only `READY` targets retain `surface_capabilities.comment=AVAILABLE` for client-owned autonomous execution.
8. Rebuild Distribution Plays from the refreshed and publisher-checked map.
9. Run the existing Growth Mandate, drafting, approval and execution control plane.
10. Run AutoResearch after the execution sweep.

A post can therefore be highly relevant and still be removed from the acquisition queue when the connected publisher cannot comment there. Relevance is not treated as publishability.

## Safety boundary

The refresh/preflight stage is read-only with respect to external platforms. It cannot publish, join a group, send a DM, reply, or mutate a Telegram profile.

External Telegram execution remains subject to all existing controls: customer-owned connection, Telegram AUTO mode, explicit Telegram automation authorization, Growth Mandate, daily publish limits, execution fee availability, duplicate/retry guards and provider reconciliation.

The preflight may update only Partizan's internal opportunity state (`READY`, `JOIN_REQUIRED`, `NO_DISCUSSION`, `NO_WRITE_ACCESS`, `PRECHECK_FAILED`) and marks unusable live opportunities stale so they are not offered as active acquisition actions.

Joining a linked discussion group is not granted by this flywheel. Opportunities that require membership remain blocked until that separate permission is explicitly delegated and implemented in the autonomous path.
