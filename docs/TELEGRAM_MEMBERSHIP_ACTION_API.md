# Telegram membership action API (Partizan agent / customer workspace)

This capability removes the need for a GitHub PR to **join each new group**.
It does **not** grant ChatGPT an installed Partizan connector by itself.
Connecting ChatGPT or another agent to the protected operator API is a separate
deployment/connector step. Never expose operator keys, customer Telegram
sessions or stored provider secrets to models.

## Contract

1. The authenticated project owner requests a read-only check:
   `POST /customer/workspace/{project_id}/telegram/linked-group-joins/preview`
   with `{"handle":"bizarreclubmsk"}`. The response contains a short-lived
   intent ID, channel ID and linked discussion ID. Only saved, native-verified,
   DIRECT-confirmed Telegram **channels** can be considered.
2. The same project owner explicitly approves the exact identities:
   `POST /customer/workspace/{project_id}/telegram/linked-group-joins/{intent_id}/approve`
   with `{"confirm_join":true,"expected_channel_id":<id>,"expected_linked_chat_id":<id>}`.
   A Telegram toggle, a product description, or a model suggestion is NOT
   approval to join. Session cookie and project ownership are mandatory.
3. An authenticated operator/agent can then execute the exact approved intent:
   `POST /v1/telegram/membership/{project_id}/linked-group-joins/{intent_id}/execute`.
   Operator authentication is mandatory. This endpoint is NOT in public API
   route allowlists.
4. Read status by project-owner GET or operator GET. The receipt includes
   `JOINED_AND_READY`, `JOINED_BUT_RESTRICTED`, `ALREADY_MEMBER` or
   `INDETERMINATE` with reason. Never infer publication permission from
   membership. Creating/sending a message remains a completely separate
   customer-approved action.

The intent expires in 20 minutes. One atomic durable reservation is made
before any attempted join; replay, crash or uncertain network outcome never
trigger a second Telegram JoinChannelRequest. Daily per-project limit is 3.
Production requires non-ephemeral RuntimeStateStore for safe process-level
idempotence.

## Safeguards

- Telegram's channel and linked group native IDs are re-read from the
  connected customer-owned session, compared with both the saved discovery
  records and owner's explicit approval.
- No arbitrary Telegram join URLs, invitations, usernames, text prompts or
  user-provided native entities are passed into JoinChannelRequest.
- Only already-saved audience and native-verified channel records can be
  previewed. The registry must independently confirm DIRECT fit. A known
  account restriction blocks the operation.
- A linked group re-point, changed channel ID, account disconnection, session
  revoke, revoked project, missing explicit owner approval, stale approval,
  identity ambiguity or duplicate execution fails closed.
- Zero Telegram sends, profile updates, follows of unrelated groups, global
  community searches, LLM calls, payment actions or marketing approvals.
- Member status is read again after joining. Failed/ambiguous requests are
  auditable and non-retryable without a **new** customer approval and intent.

## Integration with ChatGPT

The repository exposes a controlled operator endpoint, but the current ChatGPT
conversation only has a GitHub connector. It does **not** have an authenticated
Partizan app/connector capable of calling the endpoint. The required next step
is to deploy this code and register/connect an explicit Partizan connector with
operator authentication. Never hardcode the credentials or substitute a GitHub
workflow to run operator requests implicitly.

An agent integration should expose only
`preview_telegram_membership`, `request_owner_join_approval`,
`execute_approved_telegram_join` and `get_telegram_membership_status`.
The connector must not represent chat text as an in-app owner approval without
an auditable binding to the authenticated project owner.

## Acceptance checks

- Customer who owns project can preview and approve; another customer cannot.
- Operator key required to execute, and cannot execute without owner approval.
- Join occurs only once, pinned to verified native group, even under retries.
- Production receipt confirms group membership and actual writing rights.
- No message is posted before a separate "публикуй".
