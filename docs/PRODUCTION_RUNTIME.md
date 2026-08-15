# Partizan production runtime

This runbook describes the production boundary for Partizan itself. It does not modify or deploy any external product.

## Deployment model

Production is a single Partizan host running:

- PostgreSQL;
- one-shot Alembic migration container;
- FastAPI API;
- paid-control worker;
- autonomous-growth worker.

The production compose file is `docker-compose.prod.yml`. PostgreSQL is not published to the host network. Only the API loopback port is published; TLS / public routing should be owned by an explicitly configured reverse proxy on the Partizan host.

## Required server file

Create `${DEPLOY_PATH}/.env.prod` on the target host. It is intentionally never synced from GitHub.

At minimum production should set:

```dotenv
APP_ENV=production
APP_LOG_LEVEL=INFO
POSTGRES_PASSWORD=<strong-random-secret>
CONTAINER_DATABASE_URL=postgresql+asyncpg://partizan:<url-encoded-password>@postgres:5432/partizan
RUNTIME_STORAGE=database
OPERATOR_API_KEY=<strong-random-secret>
PARTIZAN_PUBLIC_BASE_URL=https://partizan.example.com
LLM_PROVIDER=openai
OPENAI_API_KEY=<secret>
SEARCH_PROVIDER=<configured-provider>
```

Provider credentials, SMTP credentials and paid-platform access tokens remain environment secrets and are added only when the corresponding capability is intentionally enabled.

## GitHub production secrets

The `production` GitHub environment may contain:

- `DEPLOY_HOST` — SSH target in `user@host` form;
- `DEPLOY_SSH_KEY` — private deployment key;
- `DEPLOY_SSH_KNOWN_HOSTS` — pinned host key line(s);
- `DEPLOY_PATH` — absolute Partizan checkout path on the host;
- `PARTIZAN_PUBLIC_URL` — optional public HTTPS base URL used for external smoke checks.

If the core SSH secrets are absent, the deploy job does not guess a host. It exits through the explicit "not configured" path.

## What deployment does

`tools/deploy_prod_remote.sh` performs this sequence on the configured Partizan host:

1. refuses to run without `.env.prod`;
2. syncs the exact GitHub release source to the configured Partizan directory;
3. validates `docker-compose.prod.yml`;
4. builds the release image;
5. starts PostgreSQL and waits for health;
6. runs `alembic upgrade head` as a one-shot service;
7. starts API and workers;
8. waits for API health;
9. runs internal `/health/live` and `/health/ready` smoke checks;
10. optionally checks public HTTPS liveness/readiness when `PARTIZAN_PUBLIC_URL` is configured.

A deploy never writes to another repository or product host.

## Manual local validation

```bash
cp .env.example .env.prod
# replace local/dev secrets before treating this as production-like
docker compose -f docker-compose.prod.yml --env-file .env.prod config --quiet
docker compose -f docker-compose.prod.yml --env-file .env.prod build
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d postgres
docker compose -f docker-compose.prod.yml --env-file .env.prod run --rm migrate
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d api paid-control-worker autonomous-growth-worker
bash tools/smoke_prod_remote.sh --local
```

## Health semantics

- `GET /health` — compatibility liveness endpoint;
- `GET /health/live` — process liveness only;
- `GET /health/ready` — returns 200 only when PostgreSQL is reachable.

Readiness deliberately does not call LLM/search/ad/social providers. A temporary third-party outage must not make the Partizan API disappear from the reverse proxy; provider-specific failures remain visible in their own execution/readiness surfaces.
