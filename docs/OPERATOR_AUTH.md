# Operator API authentication

Partizan separates public/read-only data-plane traffic from control-plane operations that create or change state, consume configured providers, or can trigger external execution.

## Configuration

Operator authentication is active when either:

- `APP_ENV` is `production` or `prod`; or
- `OPERATOR_AUTH_REQUIRED=true`.

The expected secret is read from `OPERATOR_API_KEY` and supplied by clients in:

```text
X-Partizan-Operator-Key: <deployment secret>
```

The key is deployment/process state only. It must not be persisted in the runtime store, returned from APIs, placed in URLs or committed to source control.

Production is fail-closed: if operator authentication is active but `OPERATOR_API_KEY` is absent, protected requests return `503` rather than silently disabling authentication.

## Deny-by-default mutation policy

At the FastAPI application boundary, every unsafe HTTP method is treated as control-plane by default:

- `POST`
- `PUT`
- `PATCH`
- `DELETE`

When operator authentication is active, these requests require the operator key before request-specific mutation/provider logic runs. This covers both legacy endpoints and newly added routes, even if an individual route forgets to attach its own `Depends(require_operator)` dependency.

Read methods (`GET`, `HEAD`, `OPTIONS`) remain outside this global mutation guard unless the route itself explicitly uses `require_operator`. Sensitive operational reads such as provider/control status continue to carry their route-level operator dependency.

## Intentional public mutation exceptions

There are exactly two unsafe-method exceptions to the global operator guard:

- `POST /v1/products/{product_id}/distribution-events`
- `POST /v1/products/{product_id}/distribution-events/verify`

They are product-scoped integration data-plane endpoints and authenticate with `X-Partizan-Event-Key`. The first persists a conversion event; the second validates the same attribution contract without persisting it. A Product Event Key cannot write to another product.

Tracking redirects (`GET /r/{referral_token}`) are intentionally public and may record a best-effort `VISIT` for a running experiment.

Rotating/revoking a Product Event Key, creating products, discovery/generation, action preparation, approvals, spend/learning mutations and every execution/provider operation remain inside the operator control plane in production.

## Why both global and route-level guards exist

Route-level `Depends(require_operator)` remains useful documentation and also protects sensitive GET operations. The application-level guard is the final safety net for unsafe methods, preventing a future control-plane POST/PATCH/DELETE from becoming publicly writable because of a missing decorator.
