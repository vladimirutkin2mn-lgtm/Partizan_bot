# Telegram client-owned observation and automation governance

This document covers the Phase 3 governance slice that follows the initial client-owned Telegram publishing adapter.

## Scope

The implementation adds two capabilities without enabling Telegram publishing in production by default:

1. **Post-publish observation** for messages that Partizan previously published through an authorised customer session.
2. **Explicit per-project automation authorization** for bounded execution of already-approved Telegram actions.

It does not add unsolicited DMs, community joins, invites, participant enumeration, ban evasion, account farms, or a background mass-publishing worker.

## Post-publish observation

A confirmed client-owned publish receipt already records the public target username and Telegram remote message ID. The observation service reuses those identifiers and the encrypted customer session to perform a read-only check.

Observable states are:

- `PRESENT` — the remote message is still observable;
- `REMOVED` — Telegram no longer returns the message ID;
- `INACCESSIBLE` — the account/community is no longer accessible;
- `UNKNOWN` — the provider check failed without a safe definitive interpretation.

Each observation stores:

- action ID;
- public target username;
- remote message ID;
- check timestamp;
- state;
- sanitized provider code;
- sanitized restriction signal;
- bounded observation history.

The customer `StringSession`, API hash and other provider secrets are never included in the observation payload or history.

A removal signal is intentionally factual rather than causal. `REMOVED` means that the previously confirmed message is no longer returned by Telegram; it does not automatically assert why it disappeared or whether the content was bad.

Observation is ownership-gated through the action's `DistributionExperiment` and customer project before any receipt metadata is returned or any provider call is made.

## Automation authorization

Telegram automation is stored per customer project and has explicit lifecycle states:

- `DISABLED` — no authorization record exists;
- `ENABLED` — the customer explicitly authorized bounded client-owned execution;
- `PAUSED` — authorization exists but automated execution is blocked;
- `REVOKED` — authorization was withdrawn.

Enabling automation requires an explicit `confirm_client_owned_execution=true` request. It also re-checks all current channel readiness conditions:

- Telegram client-publish provider readiness is enabled;
- client-publish API credentials are configured;
- encrypted provider secret storage is configured;
- Telegram publisher mode for the project is `CLIENT_OWNED`;
- an authorised customer Telegram session is connected.

The authorization contains a customer-selected daily maximum from 1 to 10 publishes. The automation execution path conservatively counts all confirmed Telegram publishes for the project in the last 24 hours against that maximum.

Every automated execution call re-checks authorization and readiness. Switching away from `CLIENT_OWNED`, disconnecting the account, pausing/revoking authorization, disabling provider readiness, or reaching the daily limit blocks execution.

Disconnecting the customer Telegram account also revokes project automation.

## Approval remains mandatory

This slice does **not** bypass the existing DistributionAction approval model.

The automated execution endpoint delegates to the existing client-owned publish service, which accepts only an already `APPROVED` Telegram action. Therefore:

`Opportunity/context → draft → policy/approval gates → APPROVED action → explicit project automation authorization → bounded publish`

There is no new path that can autonomously create and publish unapproved content.

## Endpoints

Customer-authenticated endpoints:

- `GET /customer/workspace/{project_id}/telegram/automation`
- `PUT /customer/workspace/{project_id}/telegram/automation`
- `POST /customer/workspace/{project_id}/telegram/automation/pause`
- `DELETE /customer/workspace/{project_id}/telegram/automation`
- `POST /customer/workspace/{project_id}/telegram/automation/actions/{action_id}/publish`
- `POST /customer/workspace/{project_id}/telegram/actions/{action_id}/observe`
- `GET /customer/workspace/{project_id}/telegram/actions/{action_id}/observation`

The existing manual client-owned publish endpoint remains unchanged.

## Production boundary

Merging or deploying this code does not operationally enable Telegram client-owned publishing.

Production remains fail-closed unless the separate client-publish configuration is deliberately provisioned:

```text
TELEGRAM_CLIENT_PUBLISH_PROVIDER=telethon
TELEGRAM_CLIENT_PUBLISH_PUBLIC_READY=true
TELEGRAM_CLIENT_PUBLISH_API_ID=...
TELEGRAM_CLIENT_PUBLISH_API_HASH=...
PROVIDER_SECRET_ENCRYPTION_KEY=...
```

These settings remain independent from Telegram research credentials/readiness.

The final Phase 3 acceptance gate still requires a real production client-owned publish and subsequent result/observation capture. This document and the code-only tests do not satisfy that operational acceptance by themselves.
