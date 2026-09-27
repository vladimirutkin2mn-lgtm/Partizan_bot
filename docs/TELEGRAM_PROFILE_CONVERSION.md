# Telegram Profile Conversion

Status: implementation in progress  
Tracker: #413

## Product decision

For Telegram community distribution, the author profile is a conversion surface.

Partizan should prefer the least-friction native destination for the promoted product:

1. Telegram channel invite link when native join attribution is available.
2. Telegram bot deep link with a start parameter.
3. Direct public Telegram link when attribution is unavailable but conversion friction matters more.
4. Partizan tracked redirect only as a fallback for external destinations or when no native attribution exists.

The customer should not have to expose a Partizan URL merely so Partizan can measure the campaign.

## Publishing modes

### Customer-owned account

The connected customer's Telegram account is part of the funnel.

Before a distribution action, Partizan may prepare a profile conversion surface consisting of:

- avatar;
- display name;
- bio;
- CTA destination;
- later: story support.

Profile mutations are opt-in and separate from message publication.

### Partizan-managed accounts

Future work will support a controlled pool of Partizan-managed identities. Each identity can have a persona and a Profile Conversion Pack. Experiments can then learn across:

- account persona;
- avatar/profile treatment;
- community and opportunity type;
- message strategy;
- CTA type;
- downstream conversion.

Managed identities must remain subject to platform limits and anti-spam safeguards.

## Safe profile mutation foundation

PR 1 implements the primitives needed before Profile Conversion Packs can be exposed to customers.

### Snapshot model

Every mutation persists a BEFORE snapshot and, after successful Telegram read-back, an AFTER_APPLY snapshot.

Snapshots include:

- project id;
- campaign id when available;
- experiment id when available;
- mutation id;
- bio;
- first and last name;
- display name;
- current Telegram avatar reference;
- timestamp.

The avatar reference is internal state and is not intended for customer-facing APIs.

### Apply flow

1. Read the current Telegram profile.
2. Persist the BEFORE snapshot.
3. Record an APPLYING mutation.
4. Apply requested bio/name changes.
5. Upload a requested avatar.
6. Re-read Telegram until the requested state is confirmed.
7. Persist AFTER_APPLY.
8. Mark the mutation APPLIED.

A profile mutation does not publish a Telegram message.

### Partial-failure behavior

If any apply step fails, Partizan attempts to restore all fields changed by that mutation and records both the original error and any rollback error.

Publication must remain blocked until a profile mutation has been verified separately.

### Explicit rollback

Rollback is intentionally fail-closed.

Before rollback, Partizan compares the currently controlled fields with the AFTER_APPLY snapshot. If they no longer match, the customer has potentially edited the profile outside Partizan. The mutation becomes ROLLBACK_BLOCKED and Partizan refuses to overwrite those edits.

If there is no drift:

1. mark ROLLING_BACK;
2. restore the previous bio/name fields;
3. restore the previous Telegram avatar, or remove the newly installed avatar when there was no previous avatar;
4. verify Telegram read-back;
5. persist AFTER_ROLLBACK;
6. mark ROLLED_BACK.

## Implementation map

- `app/telegram_client_publishing_impl.py`
  - Telegram profile read model
  - bio/name mutation primitives
  - avatar upload
  - avatar restore
  - internal safety limits
- `app/telegram_profile_conversion.py`
  - persisted snapshots
  - mutation lifecycle
  - read-back verification
  - partial-failure rollback
  - drift-protected explicit rollback
  - project/campaign/experiment association
- `tests/test_telegram_profile_conversion.py`
  - apply persistence
  - rollback
  - drift protection
  - partial failure
  - Telegram bio whitespace normalization

## Delivery plan

### PR 1: safe mutation foundation

- snapshots;
- bio/name/avatar primitives;
- read-back verification;
- rollback;
- campaign/experiment linkage.

### PR 2: ProfileConversionPack

Introduce the customer-facing aggregate:

- mode: CUSTOMER_OWNED or PARTIZAN_OWNED;
- avatar asset;
- display name;
- bio;
- CTA type/value;
- DRAFT / READY / APPLIED / ROLLED_BACK lifecycle;
- preview and explicit approval.

### PR 3: native Telegram attribution

Add measurement strategies in priority order:

- unique channel invite links;
- Telegram bot start parameters;
- tracked external redirects;
- proxy/baseline signals where direct attribution is impossible.

### PR 4: stories and learning

Add optional stories and use the deepest available conversion signal to learn which combination of persona, profile treatment, message strategy and CTA works best.

## Non-goals of PR 1

PR 1 does not:

- change the FemDom profile automatically;
- publish any Telegram comment;
- generate avatars;
- expose Profile Conversion Packs in the UI;
- create channel invite links;
- create stories;
- implement the Partizan-managed account pool.
