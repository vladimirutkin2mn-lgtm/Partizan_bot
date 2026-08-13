# Bounded outreach execution delegation

Automatic outreach execution is an explicit extra delegation on top of an active Outreach Policy and Growth Mandate.

## Required controls

Before delegation, the product must have:

- an ACTIVE Growth Mandate that explicitly allows `OUTREACH_EMAIL`;
- `autonomous_prepare=true`;
- an ACTIVE, current Outreach Policy;
- a ready owned SMTP sender with explicit From name, From address and Reply-To;
- zero autonomous follow-ups.

The product-wide `autonomous_approve` flag does **not** need to be enabled. The outreach execution delegation below is deliberately narrower: it authorizes only the bounded `OUTREACH_EMAIL` initial-message path and does not grant approval rights to other action types.

Create delegation only with explicit confirmation:

```http
POST /v1/products/{product_id}/outreach-autosend/delegate

{
  "confirm_autonomous_initial_send": true
}
```

The delegation snapshots the exact policy version, mandate version and sender identity. Any later change invalidates automatic execution until a new delegation is created.

## Volume and cooldown controls

The worker inherits the Outreach Policy limits and cannot raise them:

- no more than 5 autonomous initial sends per sender/day;
- no more than 1 autonomous initial send per contact domain/day;
- target cooldown is at least 30 days;
- domain cooldown is at least 24 hours;
- autonomous follow-up count is always 0.

Capacity is reserved atomically in RuntimeStore before the external send path. This prevents concurrent workers from racing past the delegated limits.

## Failure behavior

The existing restart-safe SMTP sender remains the only external mutation path.

- confirmed acceptance: action becomes `EXECUTED`, experiment becomes `RUNNING`;
- definitive rejection: the autonomous sweep cancels that action/experiment and may consider a different eligible target later;
- ambiguous outcome: the attempt becomes `RECONCILIATION_REQUIRED`, automatic retry is disabled, and later autonomous outreach is blocked until reconciliation;
- no valid delegation: the worker may still prepare a brief, but stops at `WAITING_APPROVAL`.

No credentials are stored in the delegation or business objects.
