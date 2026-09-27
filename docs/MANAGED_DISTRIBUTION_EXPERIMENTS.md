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
