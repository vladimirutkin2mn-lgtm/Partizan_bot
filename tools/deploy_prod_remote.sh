#!/usr/bin/env bash
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:?Set DEPLOY_HOST (user@host)}"
DEPLOY_PATH="${DEPLOY_PATH:?Set DEPLOY_PATH to an absolute remote Partizan directory}"
DEPLOY_SSH_OPTS="${DEPLOY_SSH_OPTS:-}"
PARTIZAN_PUBLIC_URL="${PARTIZAN_PUBLIC_URL:-}"

if [[ "${DEPLOY_PATH}" != /* ]]; then
  echo "Refusing deployment: DEPLOY_PATH must be absolute" >&2
  exit 1
fi

SSH_ARGS=()
if [[ -n "${DEPLOY_SSH_OPTS}" ]]; then
  # shellcheck disable=SC2206
  SSH_ARGS=( ${DEPLOY_SSH_OPTS} )
fi

ssh_remote() {
  ssh "${SSH_ARGS[@]}" "${DEPLOY_HOST}" "$1"
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
ssh_remote "cd '${DEPLOY_PATH}' && PARTIZAN_REQUIRE_PUBLIC_URL='${require_public_url}' bash tools/preflight_prod_host.sh .env.prod"

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

REMOTE_COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"

echo "==> Building Partizan release image"
ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} build"

echo "==> Starting PostgreSQL"
ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} up -d postgres"

echo "==> Applying migrations"
ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} run --rm migrate"

echo "==> Starting API and workers"
ssh_remote "cd '${DEPLOY_PATH}' && ${REMOTE_COMPOSE} up -d --remove-orphans api paid-control-worker autonomous-growth-worker"

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
fi

echo "==> Partizan production deployment verified"
