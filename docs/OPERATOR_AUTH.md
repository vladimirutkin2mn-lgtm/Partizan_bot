# Operator API authentication

Partizan separates ordinary product/discovery reads from operational mutations that can affect external distribution or paid-provider state.

## Configuration

Sensitive endpoints require the `X-Partizan-Operator-Key` header when either:

- `APP_ENV` is `production` or `prod`; or
- `OPERATOR_AUTH_REQUIRED=true`.

The expected key is read only from `OPERATOR_API_KEY` in the process environment. It is not persisted in the runtime store or returned from API responses.

Production is fail-closed: if operator auth is required but `OPERATOR_API_KEY` is missing, protected endpoints return a service-misconfigured response rather than disabling authentication.

## Protected surfaces

The MVP operator boundary covers:

- Distribution Identity / CommunityPolicy / CampaignSlot control plane;
- DistributionAction edit, approve, execute, skip and mark-executed mutations;
- Meta/TikTok paid-provider connection APIs;
- Meta/TikTok paid activation authorization and activation;
- Meta/TikTok paid control sync/pause/status;
- paid-control sweep history and reconciliation ops APIs.

Product intake, Audience Intelligence, action preparation and read-only DistributionAction/Experiment views remain outside this operator-key boundary in this slice.

## Request example

```text
X-Partizan-Operator-Key: <deployment secret>
```

Do not put the key into provider connections, database records, URLs, logs or source control. In production inject it using the deployment secret manager / environment.
