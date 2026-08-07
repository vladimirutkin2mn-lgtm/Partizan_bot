# Milestone 5 — Execution Assistant

## Goal

Take an `APPROVED` Growth Play and turn it into the first launchable acquisition experiment with
explicit human approval at every irreversible step.

The first automated delivery surface is deliberately narrow: one-to-one email outreach. Platform
DMs/posts can be prepared but remain manual until a dedicated integration exists.

## Flow

```text
APPROVED Growth Play
        ↓
resolve ChannelOpportunity
        ↓
contact extraction / user override
        ↓
UTM + referral link
        ↓
personalized outreach draft
        ↓
ExecutionPackage: PREPARED
        ↓
user edit / approve / reject
        ↓
APPROVED
        ↓
explicit Run
        ↓
DeliveryProvider sends one message
        ↓
ExecutionPackage: SENT
Experiment: RUNNING
```

There is no bulk-send endpoint and no automatic send immediately after generation.

## Contact resolution

The first `ContactExtractor` uses two sources in priority order:

1. an email explicitly supplied by the user;
2. a public email visible in Channel Hunter evidence.

If no email is available, the package falls back to `method=platform` with the discovered channel
URL. Such packages can be reviewed but cannot be auto-sent in Milestone 5.

The system does not invent contact details.

## Tracking

Every execution package receives a deterministic tracking link containing:

- `utm_source` from the channel source class;
- `utm_medium=outreach`;
- a product-specific `utm_campaign`;
- the Growth Play ID in `utm_content`;
- a deterministic `ref=partizan_<token>` referral identifier.

The user can provide `destination_url` during preparation. Otherwise the first ProductProfile
reference link is used. Product reference links remain optional for product understanding but become
useful here as actual marketing destinations.

## Message generation

The Execution Assistant drafts one transparent business outreach message. The prompt explicitly
forbids impersonation, fabricated claims, fake urgency/social proof and guaranteed results.

The draft can be edited while the package is `PREPARED`. Once approved, edits are blocked; the user
must reject/rebuild rather than silently changing an approved message.

## Approval state machine

### ExecutionPackage

- `PREPARED` — generated, editable, cannot send;
- `APPROVED` — explicit user approval, can run;
- `REJECTED` — cancelled;
- `SENT` — delivery provider accepted the message;
- `FAILED` — delivery provider failed.

### Experiment

- `DRAFT` on preparation;
- `APPROVED` when the ExecutionPackage is approved;
- `RUNNING` after successful delivery;
- `CANCELLED` if the package is rejected;
- `FINISHED` is reserved for the analytics/decision loop.

Repeated `Run` calls on a sent package are rejected, preventing accidental duplicate delivery.

## Delivery providers

### `EXECUTION_PROVIDER=mock`

Network-free provider used by local development and CI. It returns a deterministic mock delivery ID.

### `EXECUTION_PROVIDER=smtp`

Sends exactly one approved email through configured SMTP credentials.

Required configuration:

- `SMTP_HOST`;
- `SMTP_FROM_EMAIL`;
- optional username/password;
- configurable port and STARTTLS.

This provider is intentionally one-message-at-a-time. Higher-volume orchestration is not part of
this milestone.

## API

Preparation:

- `POST /v1/products/{product_id}/growth-plays/{play_id}/execution/prepare`

Review:

- `GET /v1/execution-packages/{package_id}`;
- `PATCH /v1/execution-packages/{package_id}`;
- `POST /v1/execution-packages/{package_id}/approve`;
- `POST /v1/execution-packages/{package_id}/reject`.

Execution:

- `POST /v1/execution-packages/{package_id}/run`;
- `GET /v1/experiments/{experiment_id}`.

## Persistence

Adds `ExecutionPackage` with contact/message/tracking/status/delivery metadata and extends
`Experiment` with product, execution package, tracking URL and delivery ID.

Alembic migration: `20260807_0006_execution_assistant.py`.

## Definition of Done

- only approved Growth Plays can create an execution package;
- outreach package has a resolved contact route, personalized message and tracking/referral link;
- user can edit, approve or reject before execution;
- unapproved packages cannot run;
- platform-only packages cannot be auto-sent;
- one explicit Run sends one email through the configured provider;
- successful delivery moves the Experiment to `RUNNING`;
- repeated Run does not duplicate delivery;
- Ruff and pytest pass in CI.

## Next handoff

Milestone 6 / Analytics Loop should ingest events keyed by the tracking/referral identifiers and
Experiment ID, then normalize visits, signups, paid users, revenue and spend into experiment metrics
and observed CAC.
