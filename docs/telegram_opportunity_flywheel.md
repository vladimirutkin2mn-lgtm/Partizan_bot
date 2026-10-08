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
4. Rebuild Distribution Plays from the fresh distribution map.
5. Run the existing Growth Mandate, drafting, approval and execution control plane.
6. Run AutoResearch after the execution sweep.

## Safety boundary

The refresh stage is read-only with respect to external platforms. It cannot publish, join a group, send a DM, reply, or mutate a Telegram profile.

External Telegram execution remains subject to all existing controls: customer-owned connection, Telegram AUTO mode, explicit Telegram automation authorization, Growth Mandate, daily publish limits, execution fee availability, duplicate/retry guards and provider reconciliation.

Joining a linked discussion group is not granted by this flywheel. Opportunities that require membership must remain blocked until that separate permission is explicitly delegated and implemented in the autonomous path.
