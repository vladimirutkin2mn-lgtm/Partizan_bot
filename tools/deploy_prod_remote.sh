#!/usr/bin/env bash
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:?Set DEPLOY_HOST (user@host)}"
DEPLOY_PATH="${DEPLOY_PATH:?Set DEPLOY_PATH to an absolute remote Partizan directory}"
DEPLOY_SSH_OPTS="${DEPLOY_SSH_OPTS:-}"
PARTIZAN_PUBLIC_URL="${PARTIZAN_PUBLIC_URL:-}"
# Whether this deployment owns the public HTTPS edge. Set to false on a host where another
# product already terminates TLS on 80/443; see docker-compose.shared-host.yml.
PARTIZAN_MANAGED_EDGE="${PARTIZAN_MANAGED_EDGE:-true}"
# Space-separated extra compose files, applied after the production file.
PARTIZAN_EXTRA_COMPOSE_FILES="${PARTIZAN_EXTRA_COMPOSE_FILES:-}"

if [[ "${DEPLOY_PATH}" != /* ]]; then
  echo "Refusing deployment: DEPLOY_PATH must be absolute" >&2
  exit 1
fi

if [[ "${PARTIZAN_MANAGED_EDGE}" != "true" && "${PARTIZAN_MANAGED_EDGE}" != "false" ]]; then
  echo "Refusing deployment: PARTIZAN_MANAGED_EDGE must be true or false" >&2
  exit 1
fi

for extra_compose_file in ${PARTIZAN_EXTRA_COMPOSE_FILES}; do
  if [[ ! -f "${extra_compose_file}" ]]; then
    echo "Refusing deployment: extra compose file not found: ${extra_compose_file}" >&2
    exit 1
  fi
done

SSH_ARGS=()
if [[ -n "${DEPLOY_SSH_OPTS}" ]]; then
  # shellcheck disable=SC2206
  SSH_ARGS=( ${DEPLOY_SSH_OPTS} )
fi

ssh_remote() {
  # Expanded through the +alternate form so an empty SSH_ARGS is not an unbound variable
  # under `set -u` on bash 3.2, which is what a macOS operator runs this with.
  ssh ${SSH_ARGS[@]+"${SSH_ARGS[@]}"} "${DEPLOY_HOST}" "$1"
}

echo "==> Verifying remote production environment"
ssh_remote "test -f '${DEPLOY_PATH}/.env.prod' || { echo 'Missing ${DEPLOY_PATH}/.env.prod' >&2; exit 1; }"

echo "==> Syncing exact Partizan release source"
rsync -az --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude '.env.prod' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  -e "ssh ${DEPLOY_SSH_OPTS}" \
  ./ "${DEPLOY_HOST}:${DEPLOY_PATH}/"

require_public_url=false
if [[ -n "${PARTIZAN_PUBLIC_URL}" ]]; then
  require_public_url=true
fi

echo "==> Running fail-closed production host preflight"
ssh_remote "cd '${DEPLOY_PATH}' && PARTIZAN_REQUIRE_PUBLIC_URL='${require_public_url}' PARTIZAN_MANAGED_EDGE='${PARTIZAN_MANAGED_EDGE}' PARTIZAN_EXTRA_COMPOSE_FILES='${PARTIZAN_EXTRA_COMPOSE_FILES}' bash tools/preflight_prod_host.sh .env.prod"

if [[ -n "${PARTIZAN_PUBLIC_URL}" ]]; then
  if [[ "${PARTIZAN_PUBLIC_URL}" != https://* ]]; then
    echo "Refusing public smoke: PARTIZAN_PUBLIC_URL must use https://" >&2
    exit 1
  fi
  expected_public_url="${PARTIZAN_PUBLIC_URL%/}"
  configured_public_url="$(ssh_remote "grep -E '^PARTIZAN_PUBLIC_BASE_URL=' '${DEPLOY_PATH}/.env.prod' | tail -n 1 | cut -d= -f2-" || true)"
  configured_public_url="${configured_public_url%/}"
  if [[ "${configured_public_url}" != "${expected_public_url}" ]]; then
    echo "Refusing deployment: PARTIZAN_PUBLIC_URL does not match PARTIZAN_PUBLIC_BASE_URL on host" >&2
    exit 1
  fi
fi

COMPOSE_FILE_ARGS="-f docker-compose.prod.yml"
START_SERVICES="api paid-control-worker autonomous-growth-worker"
if [[ -n "${PARTIZAN_PUBLIC_URL}" && "${PARTIZAN_MANAGED_EDGE}" == "true" ]]; then
  COMPOSE_FILE_ARGS="${COMPOSE_FILE_ARGS} -f docker-compose.edge.yml"
  START_SERVICES="${START_SERVICES} edge"
fi
for extra_compose_file in ${PARTIZAN_EXTRA_COMPOSE_FILES}; do
  COMPOSE_FILE_ARGS="${COMPOSE_FILE_ARGS} -f ${extra_compose_file}"
done
REMOTE_COMPOSE="docker compose ${COMPOSE_FILE_ARGS} --env-file .env.prod"

echo "==> Building Partizan release image"
ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} build"

if [[ -n "${PARTIZAN_PUBLIC_URL}" ]]; then
  echo "==> Verifying live Stripe launch Price"
  ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} run --rm --no-deps api python -m app.stripe_readiness"
fi

echo "==> Starting PostgreSQL"
ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} up -d postgres"

echo "==> Applying migrations"
ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} run --rm migrate"

echo "==> Starting API, workers and configured edge"
ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} up -d --remove-orphans ${START_SERVICES}"

echo "==> Waiting for API readiness"
ssh_remote "cd '${DEPLOY_PATH}' && for i in \$(seq 1 30); do if ${REMOTE_COMPOSE} exec -T api python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)\" >/dev/null 2>&1; then exit 0; fi; sleep 2; done; ${REMOTE_COMPOSE} ps; exit 1"

echo "==> Waiting for successful post-start worker sweeps"
ssh_remote "cd '${DEPLOY_PATH}' && for i in \$(seq 1 90); do if ${REMOTE_COMPOSE} exec -T api python -m app.worker_health_probe >/dev/null 2>&1; then ${REMOTE_COMPOSE} exec -T api python -m app.worker_health_probe; exit 0; fi; sleep 2; done; ${REMOTE_COMPOSE} exec -T api python -m app.worker_health_probe || true; ${REMOTE_COMPOSE} ps; exit 1"

echo "==> Internal production smoke"
ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} exec -T api python -c \"
import json
import urllib.request
for path in ('/health/live', '/health/ready'):
    with urllib.request.urlopen('http://127.0.0.1:8000' + path, timeout=5) as response:
        payload = json.loads(response.read().decode('utf-8'))
        assert response.status == 200, (path, response.status)
        assert payload.get('status') == 'ok', (path, payload)
        print(path, response.status)
\""

if [[ -n "${PARTIZAN_PUBLIC_URL}" ]]; then
  base="${PARTIZAN_PUBLIC_URL%/}"
  echo "==> Public HTTPS smoke"
  for path in /health/live /health/ready; do
    status="$(curl --silent --show-error --max-time 15 --output /dev/null --write-out '%{http_code}' "${base}${path}")"
    if [[ "${status}" != "200" ]]; then
      echo "Public smoke failed: ${base}${path} returned ${status}" >&2
      exit 1
    fi
    echo "${base}${path} 200"
  done

  echo "==> Verifying customer onboarding release is live"
  smoke_dir="$(mktemp -d)"
  start_headers="${smoke_dir}/start.headers"
  start_html="${smoke_dir}/start.html"
  curl --fail --silent --show-error --max-time 15 \
    --header 'Cache-Control: no-cache' \
    --dump-header "${start_headers}" \
    --output "${start_html}" \
    "${base}/start"
  if ! grep -Fq '/start/assets/goal-dropdown.v1.css' "${start_html}" || \
     ! grep -Fq '/start/assets/goal-dropdown.v1.js' "${start_html}"; then
    echo "Public smoke failed: ${base}/start is serving stale onboarding HTML" >&2
    rm -rf "${smoke_dir}"
    exit 1
  fi
  if ! grep -Eiq '^cache-control:.*no-store' "${start_headers}"; then
    echo "Public smoke failed: ${base}/start is missing no-store cache protection" >&2
    rm -rf "${smoke_dir}"
    exit 1
  fi

  for asset in goal-dropdown.v1.css goal-dropdown.v1.js; do
    curl --fail --silent --show-error --max-time 15 \
      --header 'Cache-Control: no-cache' \
      --output "${smoke_dir}/${asset}" \
      "${base}/start/assets/${asset}"
    if ! cmp -s "app/web/${asset}" "${smoke_dir}/${asset}"; then
      echo "Public smoke failed: ${base}/start/assets/${asset} does not match the release being deployed" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
    echo "${base}/start/assets/${asset} exact release bytes verified"
  done
  rm -rf "${smoke_dir}"
fi

echo "==> Partizan production deployment verified"
