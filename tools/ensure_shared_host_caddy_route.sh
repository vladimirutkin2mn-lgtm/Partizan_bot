#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${DEPLOY_PATH:?DEPLOY_PATH is required}"
: "${PARTIZAN_PUBLIC_URL:?PARTIZAN_PUBLIC_URL is required}"

if [[ "${DEPLOY_PATH}" != /* ]]; then
  echo "Refusing shared Caddy mutation: DEPLOY_PATH must be absolute" >&2
  exit 1
fi

public_host="${PARTIZAN_PUBLIC_URL#https://}"
public_host="${public_host%%/*}"
public_host="${public_host%%:*}"

if [[ "${public_host}" != "partizanlabs.com" ]]; then
  echo "Refusing shared Caddy mutation: unexpected public host" >&2
  exit 1
fi

echo "==> Ensuring shared-host Caddy route for ${public_host}"

ssh -o BatchMode=yes "${DEPLOY_HOST}" bash -s -- "${public_host}" "${DEPLOY_PATH}" <<'REMOTE'
set -euo pipefail

host="$1"
deploy_path="$2"
upstream="partizan-api:8000"
container_caddyfile="/etc/caddy/Caddyfile"
container_candidate="/tmp/Caddyfile.partizan.candidate"

if [[ "${host}" != "partizanlabs.com" ]]; then
  echo "shared Caddy route repair: unexpected host" >&2
  exit 1
fi
if [[ "${deploy_path}" != /* || ! -f "${deploy_path}/.env.prod" ]]; then
  echo "shared Caddy route repair: production path is unavailable" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "shared Caddy route repair: docker unavailable" >&2
  exit 1
fi

edge_network="$(grep -E '^PARTIZAN_EDGE_NETWORK=' "${deploy_path}/.env.prod" | tail -n 1 | cut -d= -f2-)"
edge_network="${edge_network%$'\r'}"
edge_network="${edge_network#\"}"
edge_network="${edge_network%\"}"
edge_network="${edge_network#\'}"
edge_network="${edge_network%\'}"
if [[ -z "${edge_network}" || ! "${edge_network}" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "shared Caddy route repair: configured edge network is missing or invalid" >&2
  exit 1
fi
if ! docker network inspect "${edge_network}" >/dev/null 2>&1; then
  echo "shared Caddy route repair: configured edge network does not exist" >&2
  exit 1
fi

docker_rows="$(docker ps --no-trunc --format '{{.ID}}\t{{.Image}}\t{{.Ports}}')"
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

api_container_id="$(
  cd "${deploy_path}" &&
  docker compose \
    -f docker-compose.prod.yml \
    -f docker-compose.shared-host.yml \
    --env-file .env.prod \
    ps -q api | head -n 1
)"
if [[ -z "${api_container_id}" ]]; then
  echo "shared Caddy route repair: Partizan API container is unavailable" >&2
  exit 1
fi

network_members="$(docker network inspect "${edge_network}" \
  --format '{{range $id, $_ := .Containers}}{{$id}}{{"\n"}}{{end}}')"
if ! printf '%s\n' "${network_members}" | grep -Fxq "${api_container_id}"; then
  echo "shared Caddy route repair: Partizan API is not attached to configured edge network" >&2
  exit 1
fi

connected_by_repair=false
if ! printf '%s\n' "${network_members}" | grep -Fxq "${tls_container_id}"; then
  if ! docker network connect "${edge_network}" "${tls_container_id}"; then
    echo "shared Caddy route repair: unable to connect proxy to Partizan edge network" >&2
    exit 1
  fi
  connected_by_repair=true
  echo "shared Caddy route repair: proxy attached to Partizan edge network"
fi

rollback_network_if_added() {
  if [[ "${connected_by_repair}" == "true" ]]; then
    docker network disconnect "${edge_network}" "${tls_container_id}" >/dev/null 2>&1 || true
  fi
}

if ! docker exec "${tls_container_id}" caddy validate \
  --config "${container_caddyfile}" --adapter caddyfile >/dev/null 2>&1; then
  rollback_network_if_added
  echo "shared Caddy route repair: existing Caddyfile is invalid; refusing mutation" >&2
  exit 1
fi

target_route_present=false
if docker exec "${tls_container_id}" sh -c \
  'grep -Fq -- "$1" /etc/caddy/Caddyfile' sh "${host}" >/dev/null 2>&1; then
  target_route_present=true
fi

if ! docker exec "${tls_container_id}" sh -c 'command -v wget >/dev/null 2>&1'; then
  rollback_network_if_added
  echo "shared Caddy route repair: proxy container cannot probe upstream safely" >&2
  exit 1
fi
if ! docker exec "${tls_container_id}" sh -c \
  'wget -q -O /dev/null -T 5 "http://$1/health/live"' sh "${upstream}"; then
  rollback_network_if_added
  echo "shared Caddy route repair: Partizan upstream is not healthy from proxy network" >&2
  exit 1
fi

echo "shared Caddy route repair: upstream preflight ok"

if [[ "${target_route_present}" == "true" ]]; then
  echo "shared Caddy route repair: target host route already present and upstream reachable"
  exit 0
fi

# The active Caddyfile is bind-mounted read-only inside the shared Caddy container.
# Resolve only the source backing this exact destination, keep it private, and edit
# that host file through a validated candidate. No unrelated mounts are printed.
caddy_source="$(docker container inspect \
  --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{"\n"}}{{end}}{{end}}' \
  "${tls_container_id}" | awk 'NF {print; exit}')"
if [[ -z "${caddy_source}" || "${caddy_source}" != /* || ! -f "${caddy_source}" || ! -w "${caddy_source}" ]]; then
  rollback_network_if_added
  echo "shared Caddy route repair: host-side Caddyfile source is unavailable or not writable" >&2
  exit 1
fi

backup="$(mktemp /tmp/Caddyfile.partizan.backup.XXXXXX)"
candidate="$(mktemp /tmp/Caddyfile.partizan.candidate.XXXXXX)"
cleanup_files() {
  rm -f "${backup}" "${candidate}"
  docker exec "${tls_container_id}" rm -f "${container_candidate}" >/dev/null 2>&1 || true
}
trap cleanup_files EXIT

cp -- "${caddy_source}" "${backup}"
cp -- "${caddy_source}" "${candidate}"
printf '\n# BEGIN PARTIZAN MANAGED ROUTE\n%s {\n\treverse_proxy %s\n}\n# END PARTIZAN MANAGED ROUTE\n' \
  "${host}" "${upstream}" >> "${candidate}"

docker cp "${candidate}" "${tls_container_id}:${container_candidate}"
if ! docker exec "${tls_container_id}" caddy validate \
  --config "${container_candidate}" --adapter caddyfile >/dev/null 2>&1; then
  rollback_network_if_added
  echo "shared Caddy route repair: candidate Caddyfile validation failed" >&2
  exit 1
fi

echo "shared Caddy route repair: candidate config valid"

restore_caddyfile() {
  cat "${backup}" > "${caddy_source}" || true
  docker exec "${tls_container_id}" caddy validate \
    --config "${container_caddyfile}" --adapter caddyfile >/dev/null 2>&1 || true
  docker exec "${tls_container_id}" caddy reload \
    --config "${container_caddyfile}" --adapter caddyfile >/dev/null 2>&1 || true
}

rollback() {
  echo "shared Caddy route repair: rolling back Caddyfile" >&2
  restore_caddyfile
  rollback_network_if_added
}

if ! cat "${candidate}" > "${caddy_source}"; then
  rollback
  echo "shared Caddy route repair: unable to update host-side Caddyfile" >&2
  exit 1
fi

if ! docker exec "${tls_container_id}" caddy validate \
  --config "${container_caddyfile}" --adapter caddyfile >/dev/null 2>&1; then
  rollback
  echo "shared Caddy route repair: active Caddyfile validation failed after update" >&2
  exit 1
fi

if ! docker exec "${tls_container_id}" caddy reload \
  --config "${container_caddyfile}" --adapter caddyfile >/dev/null 2>&1; then
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

echo "shared Caddy route repair: route restored and local TLS health verified"
REMOTE
