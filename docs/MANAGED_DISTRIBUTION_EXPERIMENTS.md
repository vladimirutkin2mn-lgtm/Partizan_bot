# Partizan-managed distribution experiments

Status: implementation in progress  
Parent architecture: `docs/TELEGRAM_PROFILE_CONVERSION.md`  
Tracker: #413

## Purpose

Partizan-managed distribution is the second execution mode beside customer-owned accounts.

It uses already-authorized managed publisher inventory. It does **not** create accounts, clone identities, evade platform restrictions, coordinate fake engagement, join communities automatically, or send unsolicited direct messages.

The goal is controlled experimentation across legitimate publisher identities:

`persona × profile strategy × message strategy × opportunity → outcome`

## Publisher experiment dimensions

A managed publisher can now carry:

- `persona`: EXPERT, CASUAL_HUMAN, NICHE_ENTHUSIAST, AESTHETIC or DISCUSSION_STARTER;
- `profile_strategy_key`: an internal label for the profile treatment currently configured on that publisher.

A reservation can request an exact persona/profile strategy and can additionally record:

- `message_strategy`;
- `experiment_arm`;
- `target_conflict_key`.

The assignment persists those dimensions so outcome learning can later compare comparable experimental arms without reconstructing the treatment from logs.

These fields are internal managed-distribution mechanics and are intentionally excluded from the customer-safe assignment response.

## Target conflict isolation

Partizan must not test several managed accounts against the same post/opportunity at the same time.

`target_conflict_key` represents the exact destination that must remain exclusive. Callers should use a canonical post/thread/opportunity identifier. If no explicit key is supplied but an `opportunity_id` exists, Partizan derives:

`opportunity:<uuid>`

Reservation is fail-closed:

- an existing RESERVED assignment with the same target key blocks every other managed publisher;
- a FULFILLED assignment keeps the same target key blocked for 24 hours;
- RELEASED/CANCELLED assignments do not block the target;
- the conflict check and reservation are executed under one process lock so two concurrent requests cannot both reserve the same target in the in-process runtime.

The older `conflict_group` remains a broader business/conflict label and is not overloaded with exact-target semantics.

## Selection behavior

Candidate selection still requires:

- active Distribution Identity;
- eligible publisher health;
- compatible platform/action/surface;
- language match;
- available publisher capacity;
- no active publisher reservation.

When requested, persona and profile strategy are exact eligibility filters. This prevents an experiment labeled as one treatment from silently running with another profile.

Ranking after eligibility remains based on:

- vertical/topic fit;
- prior outcome score;
- recent activity;
- remaining capacity.

## Guarded Telegram execution

A Partizan-managed Telegram publisher may be connected only through the operator API and only when the publisher is owned by Partizan. Partner-managed inventory cannot install a direct Partizan Telegram session.

The already-authorized Telegram `StringSession` is encrypted through the existing provider-secret store under a dedicated `MANAGED_TELEGRAM_SESSION_*` reference. Normal API responses expose only publisher identity, public Telegram username and verification timestamps; the session is never returned.

Connection installation is fail-closed:

- operator explicitly confirms management authorization;
- the session is verified against Telegram before storage;
- the live public username must exactly match the expected managed account;
- reconnecting replaces and deletes the previous encrypted session;
- a later verification fails if the session identity has changed.

Execution uses a mandatory preview contract. A preview binds together:

- managed assignment;
- exact DistributionAction and experiment;
- reserved Distribution Identity;
- Telegram account username;
- action type;
- exact target URL;
- exact content text;
- persona/profile/message/experiment-arm metadata.

Partizan computes a deterministic SHA-256 fingerprint over that material. The execute call requires both an explicit confirmation and that exact reviewed fingerprint. Any content, target, account or action change invalidates the preview before Telegram is called.

Supported targets remain deliberately narrow: public `https://t.me/...` comments, replies and standalone community posts. Private invite targets are rejected.

### Remote publication reconciliation

Before calling Telegram, Partizan stores an `IN_PROGRESS` receipt. After Telegram confirms a remote message, Partizan first persists the remote message id, URL and timestamp as `PUBLISHED_UNRECONCILED`, then fulfills the managed assignment locally.

This ordering prevents a dangerous retry case: if Telegram publishes successfully but the local fulfillment write fails, a subsequent execute call returns the existing `PUBLISHED_UNRECONCILED` receipt and **does not publish again**. An explicit reconcile operation completes the local fulfillment without sending a second Telegram message.

Provider restriction signals also fail closed. When Telegram returns a restriction signal, the managed publisher is marked `RESTRICTED` so it is removed from future candidate selection until reviewed.

Operator endpoints:

- `PUT /managed-distribution/publishers/{publisher_id}/telegram/connection`
- `GET /managed-distribution/publishers/{publisher_id}/telegram/connection`
- `POST /managed-distribution/publishers/{publisher_id}/telegram/connection/verify`
- `DELETE /managed-distribution/publishers/{publisher_id}/telegram/connection`
- `POST /managed-distribution/assignments/{assignment_id}/telegram/preview`
- `POST /managed-distribution/assignments/{assignment_id}/telegram/execute`
- `POST /managed-distribution/assignments/{assignment_id}/telegram/reconcile`
- `GET /managed-distribution/assignments/{assignment_id}/telegram/receipt`

No managed Telegram publish endpoint is called by reservation, learning, profile preparation, customer workspace loading, or comment generation. Execution is a separate mutation.

## Managed Telegram profile strategies

A `profile_strategy_key` is not treated as a label alone. For a Partizan-managed Telegram assignment it can be materialized into a reviewed live-profile treatment containing:

- display name;
- bio;
- exact visible CTA;
- optional avatar.

The treatment is bound to one RESERVED assignment, its managed publisher and Distribution Identity. The request is rejected for partner-managed inventory or for assignments without a `profile_strategy_key`.

Lifecycle:

`DRAFT -> READY -> APPLIED -> ROLLED_BACK`

The deterministic fingerprint contains assignment/product/publisher/identity, the `profile_strategy_key`, display name, bio, CTA and avatar SHA-256. Approval and apply each require the exact reviewed fingerprint.

Before apply, Partizan reads and stores the live Telegram profile snapshot. It then changes only display name, bio and the optional avatar, reads the profile back from Telegram and verifies the controlled fields. Telegram whitespace normalization in the bio is accepted, but the intended text remains exact after normalization.

If apply fails, rollback to the pre-apply snapshot is attempted immediately. Explicit rollback is also supported later, but only while the currently controlled Telegram fields still match the Partizan-applied snapshot. If a human or another system changed those fields after apply, rollback fails closed rather than overwriting the external edit.

The active managed Telegram connection records the exact applied assignment id, profile strategy key and strategy fingerprint. This gives the execution layer a machine-verifiable answer to “is the profile treatment required by this experimental arm actually live?”.

Operator endpoints:

- `POST /managed-distribution/assignments/{assignment_id}/telegram/profile-strategy`
- `GET /managed-distribution/assignments/{assignment_id}/telegram/profile-strategy`
- `GET /managed-distribution/telegram/profile-strategies/{strategy_id}/avatar`
- `GET /managed-distribution/telegram/profile-strategies/{strategy_id}/preview`
- `POST /managed-distribution/telegram/profile-strategies/{strategy_id}/approve`
- `POST /managed-distribution/telegram/profile-strategies/{strategy_id}/apply`
- `POST /managed-distribution/telegram/profile-strategies/{strategy_id}/rollback`

Profile-strategy routes never call Telegram comment/post/story publication. They only read or mutate the connected managed account profile.

## Learning contract

On fulfillment, the internal managed observation records:

- persona;
- profile strategy;
- message strategy;
- experiment arm;
- exact target conflict key;
- fulfillment timestamp.

Publisher/account identifiers remain internal and are not exposed through the customer-safe managed assignment view.

A later allocation policy can learn from these dimensions, but this slice does not auto-select a winning identity; it only provides clean experimental metadata and collision-free assignment.

## Anti-abuse boundary

Managed inventory is explicitly not an account-farm interface.

The service continues to expose no operations for:

- account creation or cloning;
- votes/upvotes/downvotes;
- mass messaging or unsolicited DMs;
- automated community joins;
- ban/restriction evasion.

The target-conflict rule additionally prevents multiple managed identities from piling onto the same opportunity.
