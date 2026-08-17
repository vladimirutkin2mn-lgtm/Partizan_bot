# Partizan public HTTPS edge

The public edge is optional. It has two mutually exclusive modes:

- **managed edge** — the default. Partizan runs its own Caddy, owns ports 80/443, and touches no external product.
- **shared host** — another product already terminates TLS on those ports. Partizan runs no Caddy of its own and is routed by the existing proxy. See [Shared-host mode](#shared-host-mode).

Choosing the wrong one is not a cosmetic mistake: starting the managed edge on a host where something else holds 80/443 fails to bind and takes the neighbouring products' public traffic down with it.

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

1. a host for Partizan — dedicated for managed-edge mode, or a shared one whose existing proxy will route the hostname;
2. DNS for the chosen hostname resolving to that host;
3. inbound TCP 80 and 443 allowed to that host;
4. outbound HTTPS/DNS connectivity required by the host for certificate issuance and configured providers.

The Compose overlay also publishes 443/UDP so HTTP/3 can be used when the host/network permits it; HTTPS over TCP 443 remains the required path.

Partizan does not guess a hostname, change DNS records or open cloud firewalls. In the default managed-edge mode it also does not reuse another project's reverse proxy; shared-host mode does, and requires the operator to make that routing change deliberately.

## Shared-host mode

Use this when Partizan is one of several products on a host and something else — typically another compose project's Caddy or nginx — already owns 80/443 and issues certificates for every subdomain.

Partizan then publishes no public port at all. The API joins the existing proxy's Docker network under the stable alias `partizan-api`, and the proxy reverse-proxies the hostname to `partizan-api:8000`.

```text
Internet :80/:443
       |
  the host's existing proxy        (owned by another project)
       |  proxy network
   partizan-api:8000               (alias on the Partizan API container)
```

Three things must line up.

**1. The overlay names the proxy's network.** In the host-owned `.env.prod`:

```dotenv
PARTIZAN_EDGE_NETWORK=the_existing_proxy_network
```

The overlay treats this as required and refuses to render without it, because there is no safe network to guess.

**2. Every deploy applies the overlay.** Deploy with:

```bash
PARTIZAN_MANAGED_EDGE=false \
PARTIZAN_EXTRA_COMPOSE_FILES=docker-compose.shared-host.yml \
DEPLOY_HOST=... DEPLOY_PATH=... \
  bash tools/deploy_prod_remote.sh
```

For the GitHub deployment, set the same two as repository or `production` environment **variables** (they carry no secret). A deploy that omits them recreates the API without the proxy network, and public routing silently breaks on the next push to `main` even though the workflow reports success.

Ad-hoc operations must apply the overlay too, or the next `up` detaches the API. Use the wrapper rather than assembling compose flags by hand:

```bash
bash /path/to/partizan/tools/compose_shared_host.sh ps
bash /path/to/partizan/tools/compose_shared_host.sh logs --tail=50 api
```

**3. The proxy routes the hostname.** That configuration belongs to the other project and is edited there, for example as a Caddy site block:

```text
partizan.example.com {
	encode zstd gzip
	reverse_proxy partizan-api:8000
}
```

Back up that file before editing it — it carries the neighbouring products' routing. If the proxy's admin API is disabled, a config reload does nothing and the proxy container has to be restarted, which briefly interrupts every product behind it. Confirm each neighbour serves again afterwards.

`PARTIZAN_PUBLIC_BASE_URL`, `PARTIZAN_PUBLIC_HOST` and `PARTIZAN_PUBLIC_URL` mean the same thing in both modes, and the public HTTPS smoke still applies — it is verifying the shared proxy's route rather than a Partizan-owned listener.

Certificates live with the proxy that issues them, so `partizan_caddy_data` and `partizan_caddy_config` are never created in this mode.

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
