#!/usr/bin/env bash
set -euo pipefail

# Static contract for everything in this repository that can reach production.
#
# 2026-09-16: a workflow running on a pull-request branch restarted the API with
# `docker compose -f docker-compose.prod.yml up -d --force-recreate api`. The recreated
# container never rejoined the shared proxy network, so partizanlabs.com served 502 for
# over an hour while that workflow's internal probe reported success and went green.
#
# Every clause below removes one link of that chain, and CI runs this on every change.

ROOT="${1:-.}"
cd "${ROOT}"

WORKFLOW_DIR=".github/workflows"
TOOLS_DIR="tools"

# check_prod_config.sh validates an environment file handed to it — CI runs it against
# .env.example — and never talks to a production host.
TOOLS_EXEMPT=("check_prod_config.sh")

failures=0

fail() {
  echo "production mutation contract: $1" >&2
  failures=$((failures + 1))
}

shopt -s nullglob

for workflow in "${WORKFLOW_DIR}"/*.yml "${WORKFLOW_DIR}"/*.yaml; do
  name="$(basename "${workflow}")"

  if ! grep -qE 'secrets\.DEPLOY_(HOST|PATH|SSH_KEY)' "${workflow}"; then
    continue
  fi

  if grep -qE '^[[:space:]]+pull_request(_target)?:' "${workflow}"; then
    fail "${name} can reach production and must not be triggered by pull_request"
  fi

  if ! grep -qE '^[[:space:]]+environment:[[:space:]]*production[[:space:]]*$' "${workflow}"; then
    fail "${name} can reach production and must declare environment: production"
  fi

  if grep -q 'docker-compose' "${workflow}"; then
    fail "${name} must drive production compose through tools/compose_shared_host.sh instead of naming compose files itself"
  fi

  if grep -qE 'compose_shared_host\.sh[[:space:]]+(up|down|start|stop|restart|run)|--force-recreate|ensure_shared_host_caddy_route\.sh|docker[[:space:]]+(restart|stop|rm|network)' "${workflow}"; then
    if ! grep -qE 'tools/(verify_public_edge|deploy_prod_remote)\.sh' "${workflow}"; then
      fail "${name} mutates production and must verify public reachability afterwards"
    fi
  fi
done

for tool in "${TOOLS_DIR}"/*.sh; do
  name="$(basename "${tool}")"

  exempt=false
  for exempt_tool in "${TOOLS_EXEMPT[@]}"; do
    if [[ "${name}" == "${exempt_tool}" ]]; then
      exempt=true
      break
    fi
  done
  if [[ "${exempt}" == "true" ]]; then
    continue
  fi

  if ! grep -q 'docker-compose.prod.yml' "${tool}"; then
    continue
  fi

  if grep -q 'docker-compose.shared-host.yml' "${tool}" ||
    grep -q 'PARTIZAN_EXTRA_COMPOSE_FILES' "${tool}"; then
    continue
  fi

  fail "${name} pins docker-compose.prod.yml without the shared-host overlay"
done

if (( failures > 0 )); then
  echo "production mutation contract: ${failures} violation(s)" >&2
  exit 1
fi

echo "production-mutation-contract=ok"
