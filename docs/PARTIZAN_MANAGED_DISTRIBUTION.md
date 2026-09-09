# Partizan Managed Distribution

## Scope

Phase 6 adds a fail-closed managed-distribution control plane on top of the existing `DistributionIdentity`, `CampaignSlot`, `DistributionAction` and publisher-mode foundations.

`PARTIZAN_MANAGED` means the customer buys an execution service fulfilled through explicitly registered Partizan-managed or partner-managed publisher inventory. It does **not** mean Partizan creates disposable accounts or automatically logs into a fleet of platform accounts.

## Production boundary

The customer-facing managed mode remains unavailable unless:

```text
MANAGED_DISTRIBUTION_PUBLIC_READY=true
```

and at least one publisher for the requested platform is currently eligible.

The default is:

```text
MANAGED_DISTRIBUTION_PUBLIC_READY=false
```

Registering inventory alone never enables the mode. Before production enablement, Partizan must separately verify operating procedures, publisher authorization, inventory health and at least one real managed experiment.

## Inventory registry

A managed publisher is layered on an existing `DistributionIdentity`. The underlying identity remains the canonical execution identity and supplies the platform-level eligibility boundary.

Managed inventory records add:

- ownership: `PARTIZAN_MANAGED` or `PARTNER_MANAGED`;
- explicit management authorization;
- internal label;
- topic / vertical coverage;
- languages;
- allowed opportunity surfaces;
- allowed actions;
- daily action capacity;
- prior outcome score;
- recent activity timestamp;
- health: `ELIGIBLE`, `PAUSED`, `RESTRICTED`, or `RETIRED`;
- partner reference for partner-managed inventory.

A publisher cannot be registered unless its underlying `DistributionIdentity` is `ACTIVE`. Managed allowed surfaces/actions must be subsets of the identity's existing eligibility contract.

## Selection

Selection is deterministic and fail-closed.

Hard gates:

1. matching platform;
2. managed health is `ELIGIBLE`;
3. underlying `DistributionIdentity` is `ACTIVE`;
4. requested action is allowed;
5. requested opportunity surface is allowed;
6. requested language matches;
7. no existing reserved managed assignment for the same publisher;
8. daily capacity remains.

Eligible candidates are ranked by:

- topic / vertical fit — up to 40 points;
- prior outcomes — up to 30 points;
- recent activity — up to 20 points;
- remaining capacity — up to 10 points.

The exact internal publisher identity and scoring reasons are operator-side data, not customer-facing account mechanics.

## Reservation and conflict prevention

A managed assignment creates an existing `CampaignSlot` in `ACTIVE` state for the selected `DistributionIdentity`.

The control plane already enforces only one active campaign slot for an identity. Phase 6 additionally excludes any publisher with an existing `RESERVED` managed assignment. This is deliberately conservative: one managed publisher cannot simultaneously serve multiple client assignments through this path.

Releasing an assignment cancels the slot. Successful fulfillment completes it.

This is a process-level scheduling foundation. If Partizan later operates multiple independent workers performing reservations concurrently, the reservation/capacity boundary must remain datastore-atomic before scaled autonomous assignment is enabled.

## Fulfillment

Phase 6 does not introduce a hidden platform account transport.

Managed fulfillment is operator/partner-confirmed:

1. a publisher is reserved;
2. a `DistributionAction` is prepared through the existing execution system;
3. the action must be explicitly `APPROVED`;
4. its platform, exact `DistributionIdentity`, experiment and product must match the managed assignment;
5. an operator records the real external reference/result;
6. the existing action is marked `EXECUTED`;
7. the campaign slot is completed;
8. the managed assignment stores cost and internal audit data.

No managed fulfillment endpoint can create accounts, vote, send mass DMs, join communities, impersonate people, rotate identities to evade restrictions, or bypass the existing action approval flow.

## Cost accounting

Managed fulfillment records three separate amounts:

```text
distribution_spend_usd
operational_cost_usd
management_fee_usd
```

They are intentionally not collapsed into one number. Phase 6 records the accounting facts; it does not move money, charge the customer, or bypass Growth Balance / billing / settlement gates.

Phase 7 can use these normalized buckets for channel economics and pricing decisions.

## Audit and customer abstraction

The internal managed assignment records:

- managed publisher ID;
- underlying `DistributionIdentity` ID;
- ownership;
- campaign slot;
- product and opportunity references;
- action type/surface;
- fulfillment action ID;
- external execution reference and URL;
- separate cost buckets;
- timestamps/status.

The customer-safe assignment view exposes the managed service outcome, ownership class, status, costs, result URL and fulfillment time, but not:

- managed publisher ID;
- `DistributionIdentity` ID;
- internal publisher label;
- partner reference.

The attached `DistributionAction` observation records the managed assignment and service/ownership outcome without leaking raw publisher inventory identifiers.

## Customer publisher mode

`PARTIZAN_MANAGED` is presented as an available publisher mode only when the global production gate is open and live eligible inventory exists for that platform.

If selected inventory later becomes paused, restricted, retired or otherwise unavailable, the stored managed choice is not treated as executable; channel state falls back to a safe available publisher mode instead of claiming managed readiness.

## Routes

Operator/control-plane routes:

```text
POST   /v1/managed-distribution/publishers
GET    /v1/managed-distribution/publishers
PATCH  /v1/managed-distribution/publishers/{publisher_id}/health
POST   /v1/managed-distribution/selection/preview
POST   /v1/products/{product_id}/managed-distribution/assignments
GET    /v1/products/{product_id}/managed-distribution/assignments
POST   /v1/managed-distribution/assignments/{assignment_id}/fulfill
DELETE /v1/managed-distribution/assignments/{assignment_id}
```

Customer-safe route:

```text
GET /customer/workspace/{project_id}/managed-distribution/assignments
```

Customer project ownership is checked before managed assignment data is returned.

## Acceptance boundary

A green code/deployment result does not close Phase 6.

Before issue #254 can close, Partizan still needs at least one real managed-distribution experiment where:

- the publisher is legitimately Partizan-managed or partner-managed;
- the assignment is eligible and conflict-safe;
- the external action is policy/platform compliant;
- actual operational cost and management fee are recorded;
- a measurable outcome is captured and attributable to the action.
