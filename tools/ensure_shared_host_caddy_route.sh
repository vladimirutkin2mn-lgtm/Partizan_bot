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

# Partizan owns exactly one file in the directory the shared proxy imports, and
# never edits the other project's Caddyfile. That directory is mounted from
# outside their deploy path, so their `rsync --delete` cannot remove the route.
# It also has to be a directory mount rather than a single-file one: a
# single-file bind mount is pinned to the inode present at container start, so a
# deploy that replaces the file leaves the proxy reading a detached copy where
# every write and every reload is a silent no-op.
conf_dir_source="$(docker container inspect \
  --format '{{range .Mounts}}{{if eq .Destination "/etc/caddy/conf.d"}}{{.Source}}{{"\n"}}{{end}}{{end}}' \
  "${tls_container_id}" | awk 'NF {print; exit}')"
if [[ -z "${conf_dir_source}" || "${conf_dir_source}" != /* || ! -d "${conf_dir_source}" || ! -w "${conf_dir_source}" ]]; then
  rollback_network_if_added
  echo "shared Caddy route repair: proxy exposes no writable imported route directory" >&2
  exit 1
fi

route_file="${conf_dir_source}/partizan.caddy"
backup="$(mktemp /tmp/Caddyfile.partizan.backup.XXXXXX)"
candidate="$(mktemp /tmp/Caddyfile.partizan.candidate.XXXXXX)"
cleanup_files() {
  rm -f "${backup}" "${candidate}"
}
trap cleanup_files EXIT

route_file_existed=false
if [[ -f "${route_file}" ]]; then
  route_file_existed=true
  cp -- "${route_file}" "${backup}"
fi

# Same response hardening the managed-edge Caddyfile.prod applies, so the two
# edge modes do not serve different security headers for the same hostname.
{
  echo "# BEGIN PARTIZAN MANAGED ROUTE"
  printf '%s {\n' "${host}"
  printf '\tencode zstd gzip\n\n'
  printf '\theader {\n'
  printf '\t\tX-Content-Type-Options "nosniff"\n'
  printf '\t\tReferrer-Policy "strict-origin-when-cross-origin"\n'
  printf '\t\tPermissions-Policy "camera=(), microphone=(), geolocation=()"\n'
  printf '\t\t-Server\n'
  printf '\t}\n\n'
  printf '\treverse_proxy %s\n' "${upstream}"
  printf '}\n'
  echo "# END PARTIZAN MANAGED ROUTE"
} > "${candidate}"

# The running config is the only proof that the route is live: the file can be
# correct while the shared Caddyfile no longer imports the directory holding it.
route_is_served() {
  docker exec "${tls_container_id}" sh -c \
    'wget -q -O - http://127.0.0.1:2019/config/apps/http/servers 2>/dev/null | grep -Fq -- "\"$1\""' \
    sh "${host}"
}

if [[ "${route_file_existed}" == "true" ]] && cmp -s "${candidate}" "${route_file}" && route_is_served; then
  echo "shared Caddy route repair: target host route already live and upstream reachable"
  exit 0
fi

restore_route_file() {
  if [[ "${route_file_existed}" == "true" ]]; then
    cat "${backup}" > "${route_file}" || true
  else
    rm -f "${route_file}"
  fi
  docker exec "${tls_container_id}" caddy reload \
    --config "${container_caddyfile}" --adapter caddyfile >/dev/null 2>&1 || true
}

rollback() {
  echo "shared Caddy route repair: rolling back managed route" >&2
  restore_route_file
  rollback_network_if_added
}

if ! cat "${candidate}" > "${route_file}"; then
  rollback
  echo "shared Caddy route repair: unable to write the managed route file" >&2
  exit 1
fi
chmod 644 "${route_file}"

if ! docker exec "${tls_container_id}" caddy validate \
  --config "${container_caddyfile}" --adapter caddyfile >/dev/null 2>&1; then
  rollback
  echo "shared Caddy route repair: config validation failed after adding the managed route" >&2
  exit 1
fi

if ! docker exec "${tls_container_id}" caddy reload \
  --config "${container_caddyfile}" --adapter caddyfile >/dev/null 2>&1; then
  rollback
  echo "shared Caddy route repair: Caddy reload failed" >&2
  exit 1
fi

# `caddy reload` exits 0 and logs "config is unchanged" when it re-reads
# identical bytes, so a successful exit code alone does not prove the route is
# live - the shared Caddyfile has to import the directory as well.
if ! route_is_served; then
  rollback
  echo "shared Caddy route repair: reloaded config does not serve the target host" >&2
  exit 1
fi

echo "shared Caddy route repair: target host present in running config"

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
