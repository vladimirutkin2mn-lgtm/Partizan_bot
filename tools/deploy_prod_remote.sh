#!/usr/bin/env bash
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:?Set DEPLOY_HOST (user@host)}"
DEPLOY_PATH="${DEPLOY_PATH:?Set DEPLOY_PATH to an absolute remote Partizan directory}"
DEPLOY_SSH_OPTS="${DEPLOY_SSH_OPTS:-}"
PARTIZAN_PUBLIC_URL="${PARTIZAN_PUBLIC_URL:-}"
PARTIZAN_RELEASE_SHA="${PARTIZAN_RELEASE_SHA:?Set PARTIZAN_RELEASE_SHA to the exact release commit}"
# Whether this deployment owns the public HTTPS edge. Set to false on a host where another
# product already terminates TLS on 80/443; see docker-compose.shared-host.yml.
PARTIZAN_MANAGED_EDGE="${PARTIZAN_MANAGED_EDGE:-true}"
# Space-separated extra compose files, applied after the production file.
PARTIZAN_EXTRA_COMPOSE_FILES="${PARTIZAN_EXTRA_COMPOSE_FILES:-}"

if [[ "${DEPLOY_PATH}" != /* ]]; then
  echo "Refusing deployment: DEPLOY_PATH must be absolute" >&2
  exit 1
fi

if [[ ! "${PARTIZAN_RELEASE_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Refusing deployment: PARTIZAN_RELEASE_SHA must be an exact 40-character Git commit SHA" >&2
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

echo "==> Deploying release ${PARTIZAN_RELEASE_SHA}"
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
RELEASE_ENV="PARTIZAN_RELEASE_SHA='${PARTIZAN_RELEASE_SHA}'"

echo "==> Building Partizan release image"
ssh_remote "cd '${DEPLOY_PATH}' && ${RELEASE_ENV} ${REMOTE_COMPOSE} build"

if [[ -n "${PARTIZAN_PUBLIC_URL}" ]]; then
  echo "==> Verifying live Stripe launch Price"
  ssh_remote "cd '${DEPLOY_PATH}' && ${RELEASE_ENV} ${REMOTE_COMPOSE} run --rm --no-deps api python -m app.stripe_readiness"
fi

echo "==> Starting PostgreSQL"
ssh_remote "cd '${DEPLOY_PATH}' && ${RELEASE_ENV} ${REMOTE_COMPOSE} up -d postgres"

echo "==> Applying migrations"
ssh_remote "cd '${DEPLOY_PATH}' && ${RELEASE_ENV} ${REMOTE_COMPOSE} run --rm migrate"

echo "==> Starting API, workers and configured edge"
ssh_remote "cd '${DEPLOY_PATH}' && ${RELEASE_ENV} ${REMOTE_COMPOSE} up -d --remove-orphans ${START_SERVICES}"

echo "==> Waiting for API readiness"
ssh_remote "cd '${DEPLOY_PATH}' && for i in \$(seq 1 30); do if ${RELEASE_ENV} ${REMOTE_COMPOSE} exec -T api python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3)\" >/dev/null 2>&1; then exit 0; fi; sleep 2; done; ${RELEASE_ENV} ${REMOTE_COMPOSE} ps; exit 1"

echo "==> Waiting for successful post-start worker sweeps"
ssh_remote "cd '${DEPLOY_PATH}' && for i in \$(seq 1 90); do if ${RELEASE_ENV} ${REMOTE_COMPOSE} exec -T api python -m app.worker_health_probe >/dev/null 2>&1; then ${RELEASE_ENV} ${REMOTE_COMPOSE} exec -T api python -m app.worker_health_probe; exit 0; fi; sleep 2; done; ${RELEASE_ENV} ${REMOTE_COMPOSE} exec -T api python -m app.worker_health_probe || true; ${RELEASE_ENV} ${REMOTE_COMPOSE} ps; exit 1"

echo "==> Internal production smoke"
ssh_remote "cd '${DEPLOY_PATH}' && ${RELEASE_ENV} ${REMOTE_COMPOSE} exec -T api python -c \"
import json
import urllib.request
for path in ('/health/live', '/health/ready'):
    with urllib.request.urlopen('http://127.0.0.1:8000' + path, timeout=5) as response:
        payload = json.loads(response.read().decode('utf-8'))
        assert response.status == 200, (path, response.status)
        assert payload.get('status') == 'ok', (path, payload)
        print(path, response.status)
with urllib.request.urlopen('http://127.0.0.1:8000/version', timeout=5) as response:
    payload = json.loads(response.read().decode('utf-8'))
    assert response.status == 200, response.status
    assert payload.get('release_sha') == '${PARTIZAN_RELEASE_SHA}', payload
    print('/version', payload.get('release_sha'))
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

  served_release_sha="$(curl --fail --silent --show-error --max-time 15 "${base}/version" | python -c 'import json,sys; print(json.load(sys.stdin).get("release_sha", ""))')"
  if [[ "${served_release_sha}" != "${PARTIZAN_RELEASE_SHA}" ]]; then
    echo "Public smoke failed: expected release ${PARTIZAN_RELEASE_SHA}, but production serves ${served_release_sha:-unknown}" >&2
    exit 1
  fi
  echo "${base}/version release ${served_release_sha} verified"

  echo "==> Verifying customer onboarding release is live"
  smoke_dir="$(mktemp -d)"
  start_headers="${smoke_dir}/start.headers"
  start_html="${smoke_dir}/start.html"
  onboarding_url="${base}/start?release=${PARTIZAN_RELEASE_SHA}"
  curl --fail --silent --show-error --max-time 15 \
    --header 'Cache-Control: no-cache' \
    --header 'Pragma: no-cache' \
    --dump-header "${start_headers}" \
    --output "${start_html}" \
    "${onboarding_url}"

  served_page_release="$(awk 'BEGIN{IGNORECASE=1} /^x-partizan-release-sha:/ {gsub("\\r", "", $2); print $2}' "${start_headers}" | tail -n 1)"
  onboarding_revision="$(awk 'BEGIN{IGNORECASE=1} /^x-partizan-onboarding-revision:/ {gsub("\\r", "", $2); print $2}' "${start_headers}" | tail -n 1)"
  if [[ "${served_page_release}" != "${PARTIZAN_RELEASE_SHA}" ]]; then
    echo "Public smoke failed: ${base}/start reached release ${served_page_release:-unknown}, expected ${PARTIZAN_RELEASE_SHA}" >&2
    rm -rf "${smoke_dir}"
    exit 1
  fi
  if [[ ! "${onboarding_revision}" =~ ^[0-9a-f]{12}$ ]]; then
    echo "Public smoke failed: ${base}/start is missing a valid onboarding revision" >&2
    rm -rf "${smoke_dir}"
    exit 1
  fi
  if ! grep -Eiq '^cache-control:.*no-store' "${start_headers}" || \
     ! grep -Eiq '^surrogate-control:.*no-store' "${start_headers}"; then
    echo "Public smoke failed: ${base}/start is missing no-store cache protection" >&2
    rm -rf "${smoke_dir}"
    exit 1
  fi

  for marker in \
    'Show Partizan what you built.' \
    'Product link' \
    'Analyze my product' \
    'Likely first audiences' \
    'id="intake-clarification-step"'; do
    if ! grep -Fq "${marker}" "${start_html}"; then
      echo "Public smoke failed: ${base}/start is missing current onboarding marker: ${marker}" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
  done
  for stale_marker in 'Paste your product.' 'Product website' 'Scan my product'; do
    if grep -Fq "${stale_marker}" "${start_html}"; then
      echo "Public smoke failed: ${base}/start is serving stale onboarding marker: ${stale_marker}" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
  done

  for asset in start.v2.js start.v2.css goal-dropdown.v1.css goal-dropdown.v1.js; do
    if ! grep -Fq "/start/assets/${asset}?v=${onboarding_revision}" "${start_html}"; then
      echo "Public smoke failed: ${base}/start does not reference versioned ${asset}" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
    curl --fail --silent --show-error --max-time 15 \
      --header 'Cache-Control: no-cache' \
      --output "${smoke_dir}/${asset}" \
      "${base}/start/assets/${asset}?v=${onboarding_revision}"
    if ! cmp -s "app/web/${asset}" "${smoke_dir}/${asset}"; then
      echo "Public smoke failed: ${base}/start/assets/${asset} does not match the release being deployed" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
    echo "${base}/start/assets/${asset} exact release bytes verified"
  done
  echo "${base}/start release ${served_page_release}, onboarding revision ${onboarding_revision} verified"

  echo "==> Verifying all browser surfaces belong to the release"
  verify_release_surface() {
    local path="$1"
    local revision_header="$2"
    local marker_text="$3"
    local prefix="$4"
    local headers="${smoke_dir}/${prefix}.headers"
    local body="${smoke_dir}/${prefix}.html"
    curl --fail --silent --show-error --max-time 15 \
      --header 'Cache-Control: no-cache' \
      --header 'Pragma: no-cache' \
      --dump-header "${headers}" \
      --output "${body}" \
      "${base}${path}"

    local release
    local revision
    release="$(awk 'BEGIN{IGNORECASE=1} /^x-partizan-release-sha:/ {gsub("\\r", "", $2); print $2}' "${headers}" | tail -n 1)"
    revision="$(awk -v header="${revision_header}" 'BEGIN{IGNORECASE=1} tolower($1) == tolower(header ":") {gsub("\\r", "", $2); print $2}' "${headers}" | tail -n 1)"
    if [[ "${release}" != "${PARTIZAN_RELEASE_SHA}" ]]; then
      echo "Public smoke failed: ${base}${path} reached release ${release:-unknown}, expected ${PARTIZAN_RELEASE_SHA}" >&2
      return 1
    fi
    if [[ ! "${revision}" =~ ^[0-9a-f]{12}$ ]]; then
      echo "Public smoke failed: ${base}${path} is missing ${revision_header}" >&2
      return 1
    fi
    if ! grep -Eiq '^cache-control:.*no-store' "${headers}" || \
       ! grep -Eiq '^surrogate-control:.*no-store' "${headers}"; then
      echo "Public smoke failed: ${base}${path} is missing shared-proxy no-store protection" >&2
      return 1
    fi
    if ! grep -Fq "${marker_text}" "${body}"; then
      echo "Public smoke failed: ${base}${path} is missing current release marker: ${marker_text}" >&2
      return 1
    fi
    printf '%s' "${revision}"
  }

  marketing_revision="$(verify_release_surface '/?release='${PARTIZAN_RELEASE_SHA} 'x-partizan-marketing-revision' 'You built the product.' 'marketing')"
  for asset in landing.v1.css landing.v1.js; do
    if ! grep -Fq "/site/assets/${asset}?v=${marketing_revision}" "${smoke_dir}/marketing.html"; then
      echo "Public smoke failed: homepage does not reference versioned ${asset}" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
    curl --fail --silent --show-error --max-time 15 \
      --output "${smoke_dir}/marketing-${asset}" \
      "${base}/site/assets/${asset}?v=${marketing_revision}"
    if ! cmp -s "app/web/${asset}" "${smoke_dir}/marketing-${asset}"; then
      echo "Public smoke failed: public ${asset} does not match the release" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
  done

  workspace_revision="$(verify_release_surface '/workspace?release='${PARTIZAN_RELEASE_SHA} 'x-partizan-workspace-revision' 'id="workspace-login-title"' 'workspace')"
  for asset in workspace.v1.js workspace.channels.v1.js workspace.projects.v1.js workspace.experiments.v1.js; do
    if ! grep -Fq "/workspace/assets/${asset}?v=${workspace_revision}" "${smoke_dir}/workspace.html"; then
      echo "Public smoke failed: workspace does not reference versioned ${asset}" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
    curl --fail --silent --show-error --max-time 15 \
      --output "${smoke_dir}/workspace-${asset}" \
      "${base}/workspace/assets/${asset}?v=${workspace_revision}"
    if ! cmp -s "app/web/${asset}" "${smoke_dir}/workspace-${asset}"; then
      echo "Public smoke failed: public workspace ${asset} does not match the release" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
  done

  app_revision="$(verify_release_surface '/app?release='${PARTIZAN_RELEASE_SHA} 'x-partizan-app-revision' 'id="product-form"' 'app')"
  for asset in partizan.v1.js execution.v2.js paid-control.v1.js; do
    if ! grep -Fq "/app/assets/${asset}?v=${app_revision}" "${smoke_dir}/app.html"; then
      echo "Public smoke failed: operator app does not reference versioned ${asset}" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
    curl --fail --silent --show-error --max-time 15 \
      --output "${smoke_dir}/app-${asset}" \
      "${base}/app/assets/${asset}?v=${app_revision}"
    if ! cmp -s "app/web/${asset}" "${smoke_dir}/app-${asset}"; then
      echo "Public smoke failed: public operator ${asset} does not match the release" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
  done

  legal_revision=""
  for entry in \
    '/privacy|What Partizan stores|privacy' \
    '/terms|Use Partizan with clear boundaries.|terms' \
    '/security|Execution should fail closed|security' \
    '/contact|Need help with Partizan?|contact'; do
    IFS='|' read -r legal_path legal_marker legal_prefix <<< "${entry}"
    revision="$(verify_release_surface "${legal_path}?release=${PARTIZAN_RELEASE_SHA}" 'x-partizan-legal-revision' "${legal_marker}" "${legal_prefix}")"
    if [[ -z "${legal_revision}" ]]; then
      legal_revision="${revision}"
    elif [[ "${legal_revision}" != "${revision}" ]]; then
      echo "Public smoke failed: legal surfaces disagree on release revision" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
    if ! grep -Fq "/site/assets/legal.v1.css?v=${revision}" "${smoke_dir}/${legal_prefix}.html"; then
      echo "Public smoke failed: ${legal_path} does not reference versioned legal CSS" >&2
      rm -rf "${smoke_dir}"
      exit 1
    fi
  done
  curl --fail --silent --show-error --max-time 15 \
    --output "${smoke_dir}/legal.v1.css" \
    "${base}/site/assets/legal.v1.css?v=${legal_revision}"
  if ! cmp -s app/web/legal.v1.css "${smoke_dir}/legal.v1.css"; then
    echo "Public smoke failed: public legal CSS does not match the release" >&2
    rm -rf "${smoke_dir}"
    exit 1
  fi

  echo "${base} marketing/workspace/app/legal release identity verified"
  rm -rf "${smoke_dir}"
fi

echo "==> Partizan production deployment verified at ${PARTIZAN_RELEASE_SHA}"
