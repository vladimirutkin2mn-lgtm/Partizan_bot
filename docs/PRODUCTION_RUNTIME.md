# Partizan production runtime

This runbook describes the production boundary for Partizan itself. It does not modify or deploy any external product.

## Deployment model

Production is a single dedicated Partizan host running:

- PostgreSQL;
- one-shot Alembic migration container;
- FastAPI API;
- paid-control worker;
- autonomous-growth worker.

The production compose file is `docker-compose.prod.yml`. PostgreSQL is not published to the host network. Only the API loopback port is published; TLS/public routing must be owned by an explicitly configured reverse proxy on the Partizan host.

## One-time host bootstrap

The host needs Docker with Compose v2, `rsync` and `openssl`. Do not reuse another project's deployment directory or secrets implicitly.

`tools/bootstrap_prod_host.sh` creates the Partizan deployment directory and a new `.env.prod` with fresh PostgreSQL/operator secrets generated on the host. It refuses to overwrite an existing file and never prints the generated secrets.

From a trusted checkout, the bootstrap script can be streamed to the dedicated server without copying a secret file through GitHub:

```bash
ssh <user@partizan-host> 'bash -s -- /absolute/partizan/path' < tools/bootstrap_prod_host.sh
```

If the public HTTPS origin is already known, set it explicitly for that one command:

```bash
PARTIZAN_PUBLIC_BASE_URL=https://partizan.example.com \
ssh <user@partizan-host> \
  'PARTIZAN_PUBLIC_BASE_URL=https://partizan.example.com bash -s -- /absolute/partizan/path' \
  < tools/bootstrap_prod_host.sh
```

The bootstrap intentionally leaves research providers in mock/unavailable mode and external credentials blank. Edit `.env.prod` directly on the Partizan host to enable only the providers/capabilities intended for production.

## Required server file

`${DEPLOY_PATH}/.env.prod` is deployment-only state and is never synced from GitHub. It must have mode `600`.

A live production configuration should include, at minimum:

```dotenv
APP_ENV=production
APP_LOG_LEVEL=INFO
RUNTIME_STORAGE=database
POSTGRES_PASSWORD=<strong-random-secret>
CONTAINER_DATABASE_URL=postgresql+asyncpg://partizan:<password>@postgres:5432/partizan
OPERATOR_AUTH_REQUIRED=true
OPERATOR_API_KEY=<strong-random-secret>
PARTIZAN_PUBLIC_BASE_URL=https://partizan.example.com
LLM_PROVIDER=openai
SEARCH_PROVIDER=openai
OPENAI_API_KEY=<secret>
```

Provider credentials, SMTP credentials and paid-platform access tokens remain environment secrets and are added only when the corresponding capability is intentionally enabled.

## Host preflight

Before image build, database start, migration or worker restart, deployment runs:

```bash
bash tools/preflight_prod_host.sh .env.prod
```

The preflight is fail-closed and checks:

- Docker and Compose v2 are available;
- `rsync` is available;
- `.env.prod` exists with mode `600`;
- PostgreSQL and operator secrets are non-placeholder and sufficiently long;
- `CONTAINER_DATABASE_URL` points to the internal `postgres` service;
- production persistence settings are not accidentally local/memory values;
- configured public base URL is an HTTPS origin with no path/query/fragment;
- OpenAI/Gemini provider modes have the matching API key;
- production Compose resolves successfully.

Set `PARTIZAN_REQUIRE_PUBLIC_URL=true` to make an HTTPS public origin mandatory. The GitHub deploy automatically does this when `PARTIZAN_PUBLIC_URL` is configured.

When GitHub `PARTIZAN_PUBLIC_URL` is present, deployment also refuses to continue if it does not exactly match the host's `PARTIZAN_PUBLIC_BASE_URL` (ignoring only a trailing slash). This keeps first-click/referral attribution on the same canonical origin that public smoke tests use.

The preflight prints no secret values.

## GitHub production secrets

The `production` GitHub environment may contain:

- `DEPLOY_HOST` — SSH target in `user@host` form;
- `DEPLOY_SSH_KEY` — private deployment key;
- `DEPLOY_SSH_KNOWN_HOSTS` — pinned host key line(s);
- `DEPLOY_PATH` — absolute Partizan checkout path on the host;
- `PARTIZAN_PUBLIC_URL` — optional public HTTPS base URL used for external smoke checks.

If the core SSH secrets are absent, the deploy job does not guess a host. It exits through the explicit "not configured" path.

## Worker heartbeat semantics

The two long-running workers write a minimal heartbeat to the shared RuntimeStateStore:

- `paid-control-worker`;
- `autonomous-growth-worker`.

A heartbeat contains only lifecycle state, interval, run count, timestamps and the exception type for the last failed sweep. Provider payloads, exception text and secrets are never persisted in the heartbeat.

`GET /v1/ops/workers/health` is operator-authenticated. A worker is healthy only after it has completed at least one successful sweep since its latest process start and while its last heartbeat is fresh relative to its configured loop interval.

On every process start, prior success is cleared before the new sweep. Therefore a stale heartbeat from the previous container cannot make a fresh deployment look healthy.

The in-container probe is:

```bash
python -m app.worker_health_probe
```

It reads `OPERATOR_API_KEY` from the API container environment, calls the local operator endpoint and prints only worker names/states. It never prints the operator key.

## What deployment does

`tools/deploy_prod_remote.sh` performs this sequence on the configured Partizan host:

1. refuses to run without the host-owned `.env.prod`;
2. syncs the exact GitHub release source while excluding all `.env*` files;
3. runs the fail-closed host preflight;
4. if public smoke is configured, verifies GitHub and host public origins match;
5. builds the release image;
6. starts PostgreSQL and waits for health;
7. runs `alembic upgrade head` as a one-shot service;
8. starts API and workers;
9. waits for API readiness;
10. waits until both workers report a successful post-start sweep through the shared database heartbeat;
11. runs internal `/health/live` and `/health/ready` smoke checks;
12. optionally checks public HTTPS liveness/readiness when `PARTIZAN_PUBLIC_URL` is configured.

A deploy never writes to another repository or product host.

## Manual production validation

On the Partizan host, from the synced checkout:

```bash
bash tools/preflight_prod_host.sh .env.prod
docker compose -f docker-compose.prod.yml --env-file .env.prod build
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d postgres
docker compose -f docker-compose.prod.yml --env-file .env.prod run --rm migrate
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d api paid-control-worker autonomous-growth-worker
docker compose -f docker-compose.prod.yml --env-file .env.prod exec -T api python -m app.worker_health_probe
bash tools/smoke_prod_remote.sh --local
```

Do not create a production `.env.prod` by copying `.env.example`; the example intentionally contains local/mock defaults.

## Health semantics

- `GET /health` — compatibility liveness endpoint;
- `GET /health/live` — process liveness only;
- `GET /health/ready` — returns 200 only when PostgreSQL is reachable;
- `GET /v1/ops/workers/health` — operator-authenticated worker-loop readiness.

API readiness deliberately does not call LLM/search/ad/social providers. A temporary third-party outage must not make the Partizan API disappear from the reverse proxy. Worker readiness is checked separately and proves the recurring control loops themselves are making progress after a deployment/restart.
