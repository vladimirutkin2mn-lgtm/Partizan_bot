#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${PARTIZAN_PUBLIC_URL:?PARTIZAN_PUBLIC_URL is required}"

public_host="${PARTIZAN_PUBLIC_URL#https://}"
public_host="${public_host%%/*}"
public_host="${public_host%%:*}"

if [[ "${public_host}" != "partizanlabs.com" ]]; then
  echo "Refusing shared Caddy mutation: unexpected public host" >&2
  exit 1
fi

echo "==> Ensuring shared-host Caddy route for ${public_host}"

ssh -o BatchMode=yes "${DEPLOY_HOST}" bash -s -- "${public_host}" <<'REMOTE'
set -euo pipefail

host="$1"
upstream="partizan-api:8000"

if [[ "${host}" != "partizanlabs.com" ]]; then
  echo "shared Caddy route repair: unexpected host" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "shared Caddy route repair: docker unavailable" >&2
  exit 1
fi

docker_rows="$(docker ps --format '{{.ID}}\t{{.Image}}\t{{.Ports}}')"
published_443_count="$(printf '%s\n' "${docker_rows}" | grep -Ec ':443->' || true)"
if [[ "${published_443_count}" != "1" ]]; then
  echo "shared Caddy route repair: expected exactly one published 443 container, found ${published_443_count}" >&2
  exit 1
fi

tls_container_id="$(printf '%s\n' "${docker_rows}" | awk -F '\t' '$3 ~ /:443->/ {print $1; exit}')"
tls_container_image="$(printf '%s\n' "${docker_rows}" | awk -F '\t' '$3 ~ /:443->/ {print $2; exit}')"
if [[ -z "${tls_container_id}" || ! "${tls_container_image}" =~ [Cc]addy ]]; then
  echo "shared Caddy route repair: published 443 container is not Caddy" >&2
  exit 1
fi

if ! docker exec "${tls_container_id}" sh -c 'test -r /etc/caddy/Caddyfile && test -w /etc/caddy/Caddyfile'; then
  echo "shared Caddy route repair: Caddyfile is not readable and writable" >&2
  exit 1
fi

if ! docker exec "${tls_container_id}" caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  echo "shared Caddy route repair: existing Caddyfile is invalid; refusing mutation" >&2
  exit 1
fi

if docker exec "${tls_container_id}" sh -c \
  'grep -Fq -- "$1" /etc/caddy/Caddyfile' sh "${host}" >/dev/null 2>&1; then
  echo "shared Caddy route repair: target host route already present; no mutation needed"
  exit 0
fi

if ! docker exec "${tls_container_id}" sh -c 'command -v wget >/dev/null 2>&1'; then
  echo "shared Caddy route repair: proxy container cannot probe upstream safely" >&2
  exit 1
fi
if ! docker exec "${tls_container_id}" sh -c \
  'wget -q -O /dev/null -T 5 "http://$1/health/live"' sh "${upstream}"; then
  echo "shared Caddy route repair: Partizan upstream is not healthy from proxy network" >&2
  exit 1
fi

echo "shared Caddy route repair: upstream preflight ok"

backup="/tmp/Caddyfile.partizan.$$.bak"
docker exec "${tls_container_id}" cp /etc/caddy/Caddyfile "${backup}"

rollback() {
  echo "shared Caddy route repair: rolling back Caddyfile" >&2
  docker exec "${tls_container_id}" cp "${backup}" /etc/caddy/Caddyfile || true
  docker exec "${tls_container_id}" caddy validate \
    --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true
  docker exec "${tls_container_id}" caddy reload \
    --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true
  docker exec "${tls_container_id}" rm -f "${backup}" >/dev/null 2>&1 || true
}

if ! docker exec "${tls_container_id}" sh -c '
  printf "\n# BEGIN PARTIZAN MANAGED ROUTE\n%s {\n\treverse_proxy %s\n}\n# END PARTIZAN MANAGED ROUTE\n" "$1" "$2" >> /etc/caddy/Caddyfile
' sh "${host}" "${upstream}"; then
  rollback
  echo "shared Caddy route repair: unable to write target route" >&2
  exit 1
fi

if ! docker exec "${tls_container_id}" caddy validate \
  --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  rollback
  echo "shared Caddy route repair: candidate Caddyfile validation failed" >&2
  exit 1
fi

echo "shared Caddy route repair: candidate config valid"

if ! docker exec "${tls_container_id}" caddy reload \
  --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  rollback
  echo "shared Caddy route repair: Caddy reload failed" >&2
  exit 1
fi

route_healthy=false
for _ in $(seq 1 15); do
  if curl --fail --silent --show-error --max-time 5 \
    --resolve "${host}:443:127.0.0.1" \
    "https://${host}/health/live" >/dev/null 2>&1; then
    route_healthy=true
    break
  fi
  sleep 2
done

if [[ "${route_healthy}" != "true" ]]; then
  rollback
  echo "shared Caddy route repair: local SNI health check failed after reload" >&2
  exit 1
fi

docker exec "${tls_container_id}" rm -f "${backup}"
echo "shared Caddy route repair: route restored and local TLS health verified"
REMOTE
