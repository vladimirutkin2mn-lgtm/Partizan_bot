# Product integration verification

Before enabling real conversion delivery, a product backend can validate the exact Partizan event contract without writing an analytics event.

## Endpoint

```http
POST /v1/products/{product_id}/distribution-events/verify
X-Partizan-Event-Key: ptz_evt_<secret>
Content-Type: application/json
```

Send the same JSON shape that the real `/distribution-events` endpoint accepts.

Example:

```json
{
  "event_id": "e09079bc-e510-463d-994e-a6e66175295c",
  "event_type": "PAID",
  "experiment_id": "f65824e6-c9ca-4988-b486-2f3f8e2299e0",
  "actor_id": "integration-check-user",
  "revenue": 6.9
}
```

A successful response is explicit that no analytics fact was stored:

```json
{
  "valid": true,
  "persisted": false,
  "event_id": "e09079bc-e510-463d-994e-a6e66175295c",
  "experiment_id": "f65824e6-c9ca-4988-b486-2f3f8e2299e0",
  "event_type": "PAID",
  "attributed_by": "experiment_id",
  "duplicate": false,
  "detail": "Event is valid and was not persisted"
}
```

## What is verified

The endpoint uses the real production contract and checks:

- the Product Event Key;
- Pydantic event schema and supported event type;
- revenue rules (`revenue` only on `PAID`);
- at least one real attribution identifier;
- resolution of `experiment_id`, `action_id` or `referral_token`;
- product ownership of the resolved experiment;
- that the experiment is `RUNNING` or `FINISHED` and therefore measurable;
- event-id compatibility when that `event_id` already exists.

## What is deliberately not done

Verification never:

- persists a conversion event;
- increments VISIT / SIGNUP / ACTIVATED / PAID;
- changes revenue, CAC or ROAS;
- triggers Growth Manager learning;
- changes an experiment or action state;
- sends anything to the integrated product;
- modifies any external repository.

If an identical `event_id` is already present in real analytics, verification returns `duplicate=true` but does not rewrite it. If that ID belongs to a materially different event, verification fails exactly like real ingestion would.

## Suggested rollout

1. Create the Product Event Key in Partizan.
2. Start a real Partizan experiment so it is `RUNNING`.
3. From the product backend, construct one representative event payload.
4. Send it to `/distribution-events/verify`.
5. Confirm `valid=true` and `persisted=false`.
6. Switch the product's outbox destination to `/distribution-events` for real business events.
7. Use the Partizan `Проверка интеграции` workspace to confirm that real funnel events begin appearing.

Do not use the verification endpoint as a health check on every customer event. It exists for setup/release verification; normal production delivery should go directly to the idempotent real ingestion endpoint.
