#!/usr/bin/env bash
set -euo pipefail

MODE="remote"
if [[ "${1:-}" == "--local" ]]; then
  MODE="local"
fi

COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.prod"

run_check() {
  ${COMPOSE} exec -T api python -c "
import json
import urllib.request
for path in ('/health/live', '/health/ready'):
    with urllib.request.urlopen('http://127.0.0.1:8000' + path, timeout=5) as response:
        payload = json.loads(response.read().decode('utf-8'))
        assert response.status == 200, (path, response.status)
        assert payload.get('status') == 'ok', (path, payload)
        print(path, response.status)
"
}

if [[ "${MODE}" == "local" ]]; then
  run_check
  exit 0
fi

DEPLOY_HOST="${DEPLOY_HOST:?Set DEPLOY_HOST (user@host)}"
DEPLOY_PATH="${DEPLOY_PATH:?Set DEPLOY_PATH}"
DEPLOY_SSH_OPTS="${DEPLOY_SSH_OPTS:-}"

if [[ "${DEPLOY_PATH}" != /* ]]; then
  echo "DEPLOY_PATH must be absolute" >&2
  exit 1
fi

ssh ${DEPLOY_SSH_OPTS} "${DEPLOY_HOST}" "cd '${DEPLOY_PATH}' && ${COMPOSE} exec -T api python -c \"
import json
import urllib.request
for path in ('/health/live', '/health/ready'):
    with urllib.request.urlopen('http://127.0.0.1:8000' + path, timeout=5) as response:
        payload = json.loads(response.read().decode('utf-8'))
        assert response.status == 200, (path, response.status)
        assert payload.get('status') == 'ok', (path, payload)
        print(path, response.status)
\""
