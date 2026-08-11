# Partizan conversion event integration

Partizan can only optimize distribution if downstream product events are attributed back to a real `DistributionExperiment`.

The production integration is server-to-server. Do **not** put the Partizan Event Key into browser JavaScript, a public mobile bundle, query strings, tracking links, logs, or analytics properties.

## 1. Create or rotate a Product Event Key

An operator creates the key for one product:

```http
POST /v1/products/{product_id}/distribution-event-key
X-Partizan-Operator-Key: <operator key>
```

The response contains `event_key` once:

```json
{
  "product_id": "...",
  "configured": true,
  "key_hint": "ptz_evt_…Ab12cd",
  "created_at": "2026-08-11T10:00:00Z",
  "event_key": "ptz_evt_<high-entropy-secret>"
}
```

Store that value in the client product's secret manager / server environment.
Partizan persists only a SHA-256 digest and a non-secret hint. Calling the status endpoint later never returns the plaintext key.

Rotating the key immediately invalidates the previous key. Revoking deletes the active key:

```http
GET    /v1/products/{product_id}/distribution-event-key
POST   /v1/products/{product_id}/distribution-event-key
DELETE /v1/products/{product_id}/distribution-event-key
```

These management endpoints are operator-protected in production.

## 2. Preserve Partizan attribution on entry

A `DistributionExperiment.tracking_url` already carries attribution parameters such as:

- `ptz_experiment` — DistributionExperiment ID;
- `ptz_action` — DistributionAction ID;
- `utm_content` — DistributionPlay ID.

When a visitor reaches the product, persist `ptz_experiment` or `ptz_action` in a first-party server-side session / user record. Do not rely on the browser keeping Partizan state forever.

Some integrations can instead preserve the experiment `referral_token`; the ingestion endpoint accepts it too.

At least one of these attribution identifiers must be sent with every event:

- `experiment_id`;
- `action_id`;
- `referral_token`.

The Event Key is bound to its ProductProfile. Even a valid key cannot write an event to an experiment belonging to another product.

## 3. Send conversion events from the product backend

Endpoint:

```http
POST /v1/products/{product_id}/distribution-events
X-Partizan-Event-Key: ptz_evt_<secret>
Content-Type: application/json
```

Supported event types:

- `VISIT`
- `SIGNUP`
- `ACTIVATED`
- `PAID`

Example signup:

```json
{
  "event_id": "59dafccd-35a7-49df-a8a7-47b8500cc410",
  "event_type": "SIGNUP",
  "experiment_id": "f65824e6-c9ca-4988-b486-2f3f8e2299e0",
  "actor_id": "user_18429"
}
```

Example payment:

```json
{
  "event_id": "e09079bc-e510-463d-994e-a6e66175295c",
  "event_type": "PAID",
  "action_id": "cb39fc16-9b66-4b2e-bffa-b4c3e38a5e14",
  "actor_id": "user_18429",
  "revenue": 6.9,
  "properties": {
    "currency": "USD",
    "plan": "monthly"
  }
}
```

`revenue` is allowed only for `PAID`. Use `0` for every other event type.

## 4. Idempotency and user identity

### `event_id`

Generate one UUID per real business event and reuse that same UUID when retrying delivery.
Partizan treats an identical repeated `event_id` as a duplicate instead of double-counting it.

Do not generate a fresh UUID for every HTTP retry.

### `actor_id`

Use a stable, non-secret product-side user/customer identifier. Partizan uses `actor_id` to count unique `SIGNUP`, `ACTIVATED`, and `PAID` users.

Good examples:

- internal user UUID;
- stable hashed customer ID;
- Telegram user ID transformed according to the product's privacy policy.

Do not send passwords, access tokens, card data, session cookies, raw authorization headers, or other credentials in `actor_id` or `properties`.

## 5. Python server example

```python
import os
import uuid
import httpx

PARTIZAN_URL = os.environ["PARTIZAN_URL"]
PARTIZAN_PRODUCT_ID = os.environ["PARTIZAN_PRODUCT_ID"]
PARTIZAN_EVENT_KEY = os.environ["PARTIZAN_EVENT_KEY"]


def report_paid(*, experiment_id: str, user_id: str, revenue: float) -> None:
    response = httpx.post(
        f"{PARTIZAN_URL}/v1/products/{PARTIZAN_PRODUCT_ID}/distribution-events",
        headers={"X-Partizan-Event-Key": PARTIZAN_EVENT_KEY},
        json={
            "event_id": str(uuid.uuid4()),
            "event_type": "PAID",
            "experiment_id": experiment_id,
            "actor_id": user_id,
            "revenue": revenue,
        },
        timeout=10,
    )
    response.raise_for_status()
```

In production, persist the generated `event_id` with the payment/outbox row so retries reuse it.

## 6. Node.js server example

```js
import crypto from "node:crypto";

export async function reportActivated({ experimentId, userId }) {
  const response = await fetch(
    `${process.env.PARTIZAN_URL}/v1/products/${process.env.PARTIZAN_PRODUCT_ID}/distribution-events`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Partizan-Event-Key": process.env.PARTIZAN_EVENT_KEY,
      },
      body: JSON.stringify({
        event_id: crypto.randomUUID(),
        event_type: "ACTIVATED",
        experiment_id: experimentId,
        actor_id: userId,
      }),
    },
  );

  if (!response.ok) throw new Error(`Partizan event failed: ${response.status}`);
}
```

Again, an application with delivery retries should persist the generated `event_id` and reuse it.

## 7. Recommended product event mapping

Keep the mapping stable across experiments:

| Partizan event | Product meaning |
|---|---|
| `VISIT` | attributable product/landing/bot entry |
| `SIGNUP` | account/bot onboarding started or completed, according to one fixed definition |
| `ACTIVATED` | user reached the product's core value event |
| `PAID` | successful captured payment / recognized paid conversion |

Do not redefine `ACTIVATED` from campaign to campaign. Growth Manager comparisons only make sense when the funnel semantics stay consistent.

## 8. Delivery pattern

For payments and other high-value events, prefer an application outbox / retry queue:

```text
business transaction
  → save business event + stable event_id
  → async delivery to Partizan
  → HTTP 201 = delivered
  → retry network/5xx failures with the same event_id
```

A repeated valid event returns `duplicate=true` and does not double-count the conversion.

## 9. Legacy/operator analytics writes

The generic endpoints still exist for internal/operator workflows, but production mutation access is operator-protected:

- `POST /v1/distribution-analytics/events`
- `POST /v1/distribution-experiments/{experiment_id}/spend`
- `POST /v1/distribution-experiments/{experiment_id}/finish`

Client products should use the product-bound `/distribution-events` integration instead of the generic analytics write endpoint.
