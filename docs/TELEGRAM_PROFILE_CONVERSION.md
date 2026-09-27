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

## Profile-surface learning

PR 4 starts with an observation layer that joins each ProfileConversionPack to the exact distribution action and experiment that used it.

For every non-archived pack, Partizan exposes:

- a stable `profile_treatment_key` derived only from display name, bio, CTA and avatar, independent of action/experiment ids;
- message strategy and conversion mechanism from the bound distribution action;
- CTA type and native attribution strategy;
- visits, native joins, bot starts, signups, activations and paid users;
- replies/removals for community feedback;
- revenue;
- the deepest observed funnel signal.

This keeps the learning unit explicit:

`profile treatment × message strategy × CTA strategy × distribution context → observed outcome`

The customer workspace endpoint is:

- `GET /customer/workspace/{project_id}/telegram/profile-learning`

This slice intentionally does not auto-select a winner yet. It creates the comparable observations needed for later allocation across Partizan-managed identities and profile variants without conflating a message experiment with a profile experiment.

Stories are the next PR 4 slice. They will add a proxy-signal surface for scenarios where direct `JOIN` / `BOT_START` attribution is not available.

## Non-goals of PR 1

PR 1 does not:

- change the FemDom profile automatically;
- publish any Telegram comment;
- generate avatars;
- expose Profile Conversion Packs in the UI;
- create channel invite links;
- create stories;
- implement the Partizan-managed account pool.


## ProfileConversionPack lifecycle

PR 2 adds the customer-facing aggregate that sits above the raw mutation layer.

A pack is bound to one Telegram distribution action and therefore inherits:

- customer project;
- product;
- distribution experiment;
- campaign slot when present.

The current customer-owned lifecycle is:

`DRAFT -> READY -> APPLIED -> ROLLED_BACK`

A pack may also be `ARCHIVED` when it is not applied.

### Exact review contract

Each pack has a deterministic fingerprint over:

- project/product/action/experiment identity;
- an exact fingerprint of the bound Telegram action target and content;
- mode;
- name;
- display name;
- bio;
- CTA type and value;
- avatar SHA-256;
- story flag.

Approval requires the browser to send the exact fingerprint it reviewed. Editing a DRAFT or READY pack resets approval and produces a new fingerprint. Apply requires the same fingerprint to still be both current and approved.

If the bound Telegram action target or content changes after pack creation, the pack is stale and must be recreated. Applying a pack is permitted only after the Telegram action itself reaches `APPROVED`, so Partizan does not mutate the customer's live profile for an unapproved message.

This mirrors the exact-review protection already used by Telegram publishing.

### Customer-owned pack API

The customer workspace exposes:

- `GET /customer/workspace/{project_id}/telegram/profile-packs`
- `POST /customer/workspace/{project_id}/telegram/profile-packs`
- `GET /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}`
- `PUT /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}`
- `GET /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/preview`
- `GET /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/avatar`
- `POST /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/approve`
- `POST /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/apply`
- `POST /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/rollback`
- `DELETE /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}`

Avatar bytes remain internal state. Customer responses expose only safe metadata plus an authenticated preview path.

### Native CTA rules

A customer-owned pack must contain the exact CTA URL in the Telegram bio.

Supported CTA classes are:

- Telegram channel invite;
- Telegram public link;
- Telegram bot deep link;
- Partizan tracked redirect;
- external URL.

Telegram-native CTA types are validated to use Telegram hosts. Invite links must use an invite form, and bot links must carry a `start` parameter.

Native Telegram measurement is implemented in the next attribution slice; PR 2 establishes the typed contract now.

### Publish coupling

Profile preparation and message publication are still separate operations.

If no profile pack is associated with a Telegram action, existing publishing behavior is unchanged.

If a non-archived pack exists for an action, customer-owned publication is blocked until that reviewed pack reaches `APPLIED`. This prevents the customer from reviewing one profile treatment and accidentally publishing while a different profile is live.

### Avatar storage

The PR 2 MVP accepts JPEG, PNG and WebP avatar bytes as base64 input with:

- MIME/signature consistency checks;
- 5 MiB pack-level safety limit;
- SHA-256 fingerprinting;
- authenticated preview delivery.

The raw image content is never returned in the normal pack JSON response.

### Deferred by design

PR 2 defines but does not yet activate:

- Partizan-managed account packs;
- Telegram stories;
- automatic profile-pack generation;
- native invite-link creation;
- bot-start attribution;
- multi-armed-bandit allocation.

Those remain separate tracked slices so that profile mutation, approval and attribution can be reviewed independently.


## Native Telegram attribution

PR 3 removes the requirement to expose a Partizan redirect when Telegram can measure the product conversion natively.

### Channel invite strategy

For a customer-owned Telegram profile pack whose CTA is a plain public channel URL such as `https://t.me/example`:

1. the pack remains in `DRAFT`;
2. Partizan creates an experiment-specific exported invite link using the connected customer Telegram session;
3. Partizan replaces the public channel URL in the pack bio with the returned `t.me/+...` invite;
4. because the pack content changed, the pack receives a new fingerprint and still requires exact customer approval;
5. after the distribution experiment runs, Partizan reads the exported invite usage counter;
6. growth in that counter is recorded as a `JOIN` conversion on the exact distribution experiment.

The end user sees only a native Telegram link. No Partizan domain is inserted into the conversion path.

### Bot deep-link strategy

For a customer-owned profile pack whose CTA is a plain Telegram bot URL such as `https://t.me/examplebot`:

1. the pack remains in `DRAFT`;
2. Partizan reads the exact DistributionExperiment referral token;
3. Partizan rewrites the CTA to `https://t.me/examplebot?start=<referral_token>`;
4. the rewritten pack receives a new fingerprint and still requires exact customer approval;
5. the promoted bot sends a `BOT_START` event to the authenticated product event endpoint using the Telegram `start` value as `referral_token`;
6. Partizan resolves that token to the exact experiment and exposes `bot_starts` in experiment metrics.

The user still sees a native Telegram URL and does not pass through a Partizan redirect.

### Attribution state

Native attribution is stored separately from the profile pack and records:

- project/product/profile-pack/action/experiment identity;
- public source channel URL;
- native invite URL;
- current Telegram usage count;
- count already committed to analytics;
- latest join delta;
- join-request count where Telegram exposes it;
- last sync and last analytics event;
- pending/error state.

Provider usage is treated as cumulative. Sync never reduces a previously observed join count.

### Analytics delivery

Distribution analytics now recognizes:

- `JOIN`: native Telegram channel membership conversion;
- `BOT_START`: native Telegram bot deep-link conversion, reserved for the bot-attribution slice.

Experiment metrics expose `joins` and `bot_starts` alongside visits, signups, activation and paid conversions.

A Telegram invite can accumulate usage before a DistributionExperiment becomes `RUNNING`. In that case Partizan preserves the provider count as `analytics_pending` and retries ingestion on later sync rather than dropping the signal.

Native attribution sync is also attached to the normal post-publish Telegram observation flow. Each time Partizan verifies that its published comment/post is still present, it also refreshes the invite usage counter for the same action. Attribution failure is read-only and does not turn a successfully published action into a failed publication.

### Customer API

The customer workspace exposes:

- `POST /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/native-attribution/provision` for channel invites
- `POST /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/native-attribution/provision-bot` for bot deep links
- `GET /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/native-attribution`
- `POST /customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/native-attribution/sync`

Provisioning is allowed only while the profile pack is still `DRAFT`, because provisioning changes the CTA and therefore must happen before customer approval.

### Current scope and permissions

The channel-invite strategy supports public Telegram channels for which the connected Telegram account has permission to create invite links. If Telegram rejects invite creation because the account lacks channel administration rights, provisioning fails closed and the existing profile pack remains unapproved.

The bot deep-link strategy supports plain public bot usernames and uses the experiment referral token as the Telegram `start` parameter. Product-side bot code must report `BOT_START` through the existing authenticated distribution-event integration. Stories remain a separate proxy-signal layer for cases where direct conversion attribution is unavailable.
