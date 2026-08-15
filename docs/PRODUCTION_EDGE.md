# Partizan public HTTPS edge

The public edge is optional and belongs to the Partizan production deployment. It is not shared with, and does not modify, any external product.

## Architecture

`docker-compose.prod.yml` remains the private runtime:

```text
PostgreSQL (Docker network only)
        |
       API :8000 (host 127.0.0.1 only)
        |
 paid-control + autonomous-growth workers
```

When an explicit public Partizan origin is configured, deployment adds `docker-compose.edge.yml`:

```text
Internet :80/:443
       |
     Caddy
       |
   api:8000 (Docker network)
```

Caddy is the only service in the repository-managed stack that publishes public ports. PostgreSQL remains private, and the API's direct host binding remains loopback-only.

## Explicit configuration

The host-owned `.env.prod` must contain matching values:

```dotenv
PARTIZAN_PUBLIC_BASE_URL=https://partizan.example.com
PARTIZAN_PUBLIC_HOST=partizan.example.com
```

`PARTIZAN_PUBLIC_HOST` is a DNS hostname only: no scheme, port, path, query or fragment.

The GitHub `production` environment must set the same canonical origin in:

```text
PARTIZAN_PUBLIC_URL=https://partizan.example.com
```

Preflight/deploy refuse to continue when these values disagree. This matters because the same public origin is used by referral attribution, the Product Integration Kit and external smoke checks.

If no public URL is configured, deployment does not load the edge overlay and does not publish ports 80/443.

## External prerequisites

Before the first public deployment, the operator must explicitly arrange infrastructure outside the repository:

1. a dedicated Partizan host;
2. DNS for the chosen hostname resolving to that host;
3. inbound TCP 80 and 443 allowed to that host;
4. outbound HTTPS/DNS connectivity required by the host for certificate issuance and configured providers.

The Compose overlay also publishes 443/UDP so HTTP/3 can be used when the host/network permits it; HTTPS over TCP 443 remains the required path.

Partizan does not guess a hostname, change DNS records, open cloud firewalls, or reuse another project's reverse proxy configuration.

## TLS state

`Caddyfile.prod` uses Caddy's automatic HTTPS for the explicitly configured hostname. Certificate/account state is stored in named host volumes:

- `partizan_caddy_data`;
- `partizan_caddy_config`.

No certificate private key or ACME state is committed to GitHub or copied through the deployment workflow.

## Security boundary

Public traffic is reverse-proxied to `api:8000` over the internal Docker network. The edge adds conservative response headers and removes the `Server` response header.

Publishing the API does not publish an unauthenticated control plane:

- production `POST/PUT/PATCH/DELETE` routes are deny-by-default behind `X-Partizan-Operator-Key`;
- the two conversion data-plane POST routes use their separate product-scoped `X-Partizan-Event-Key`;
- sensitive operational GET routes, including worker health, carry route-level operator authentication.

## Validation

CI validates both the combined Compose configuration and the actual Caddyfile syntax.

On a production checkout with `.env.prod` configured:

```bash
PARTIZAN_ENV_FILE=.env.prod \
docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.edge.yml \
  --env-file .env.prod \
  config --quiet
```

The normal production deploy automatically selects this overlay when `PARTIZAN_PUBLIC_URL` is set. After API and worker readiness, the GitHub runner requires `200` from public HTTPS `/health/live` and `/health/ready` before the deployment is considered verified.
