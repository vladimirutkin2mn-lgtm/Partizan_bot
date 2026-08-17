#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env.prod}"
REQUIRE_PUBLIC_URL="${PARTIZAN_REQUIRE_PUBLIC_URL:-false}"
# Mirrors tools/deploy_prod_remote.sh so the validated compose set is the deployed one.
MANAGED_EDGE="${PARTIZAN_MANAGED_EDGE:-true}"
EXTRA_COMPOSE_FILES="${PARTIZAN_EXTRA_COMPOSE_FILES:-}"

fail() {
  echo "production preflight: $*" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command is missing: $1"
}

env_value() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    printf ''
    return
  fi
  local value="${line#*=}"
  value="${value#\"}"
  value="${value%\"}"
  value="${value#\'}"
  value="${value%\'}"
  printf '%s' "${value}"
}

require_value() {
  local key="$1"
  local value
  value="$(env_value "${key}")"
  [[ -n "${value}" ]] || fail "${key} is required in ${ENV_FILE}"
}

reject_placeholder() {
  local key="$1"
  local value
  value="$(env_value "${key}")"
  case "${value,,}" in
    ""|partizan|change-me|changeme|replace-me|replace_me|example|secret|password)
      fail "${key} still uses an empty/default placeholder value"
      ;;
  esac
  [[ "${value}" != *"<"* && "${value}" != *">"* ]] || fail "${key} still contains a placeholder"
}

need_command docker
need_command rsync
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is unavailable"

[[ "${MANAGED_EDGE}" == "true" || "${MANAGED_EDGE}" == "false" ]] || \
  fail "PARTIZAN_MANAGED_EDGE must be true or false"

for extra_compose_file in ${EXTRA_COMPOSE_FILES}; do
  [[ -f "${extra_compose_file}" ]] || \
    fail "extra compose file is missing from the release: ${extra_compose_file}"
done

[[ -f "${ENV_FILE}" ]] || fail "environment file not found: ${ENV_FILE}"

mode="$(stat -c '%a' "${ENV_FILE}" 2>/dev/null || true)"
[[ "${mode}" == "600" ]] || fail "${ENV_FILE} permissions must be 600 (current: ${mode:-unknown})"

require_value POSTGRES_PASSWORD
reject_placeholder POSTGRES_PASSWORD
postgres_password="$(env_value POSTGRES_PASSWORD)"
(( ${#postgres_password} >= 24 )) || fail "POSTGRES_PASSWORD must be at least 24 characters"

require_value OPERATOR_API_KEY
reject_placeholder OPERATOR_API_KEY
operator_key="$(env_value OPERATOR_API_KEY)"
(( ${#operator_key} >= 32 )) || fail "OPERATOR_API_KEY must be at least 32 characters"

app_env="$(env_value APP_ENV)"
if [[ -n "${app_env}" && "${app_env}" != "production" && "${app_env}" != "prod" ]]; then
  fail "APP_ENV must be production/prod when set in ${ENV_FILE}"
fi

runtime_storage="$(env_value RUNTIME_STORAGE)"
if [[ -n "${runtime_storage}" && "${runtime_storage}" != "database" ]]; then
  fail "RUNTIME_STORAGE must be database when set in ${ENV_FILE}"
fi

require_value CONTAINER_DATABASE_URL
container_database_url="$(env_value CONTAINER_DATABASE_URL)"
[[ "${container_database_url}" == postgresql+asyncpg://partizan:* ]] || \
  fail "CONTAINER_DATABASE_URL must use the partizan asyncpg production DSN"
[[ "${container_database_url}" == *@postgres:5432/partizan* ]] || \
  fail "CONTAINER_DATABASE_URL must target the internal postgres service"

public_base_url="$(env_value PARTIZAN_PUBLIC_BASE_URL)"
public_host="$(env_value PARTIZAN_PUBLIC_HOST)"
if [[ "${REQUIRE_PUBLIC_URL}" == "true" && -z "${public_base_url}" ]]; then
  fail "PARTIZAN_PUBLIC_BASE_URL is required for public production smoke"
fi
if [[ -n "${public_base_url}" ]]; then
  [[ "${public_base_url}" =~ ^https://[^/?#]+/?$ ]] || \
    fail "PARTIZAN_PUBLIC_BASE_URL must be an HTTPS origin without path/query/fragment"
  expected_public_host="${public_base_url#https://}"
  expected_public_host="${expected_public_host%/}"
  [[ -n "${public_host}" ]] || fail "PARTIZAN_PUBLIC_HOST is required when public base URL is set"
  [[ "${public_host}" == "${expected_public_host}" ]] || \
    fail "PARTIZAN_PUBLIC_HOST must exactly match the hostname in PARTIZAN_PUBLIC_BASE_URL"
  [[ "${public_host}" =~ ^[A-Za-z0-9.-]+$ ]] || \
    fail "PARTIZAN_PUBLIC_HOST must be a DNS hostname without scheme, port or path"
  if [[ "${MANAGED_EDGE}" == "true" ]]; then
    [[ -f Caddyfile.prod && -f docker-compose.edge.yml ]] || \
      fail "public HTTPS edge files are missing from the release"
  fi

  # The public customer funnel sells both the Acquisition Plan and Autopilot.
  # Do not publish buttons that silently lead to unconfigured billing.
  for stripe_key in \
    STRIPE_SECRET_KEY \
    STRIPE_WEBHOOK_SECRET \
    STRIPE_LAUNCH_PRICE_ID \
    STRIPE_AUTOPILOT_PRICE_ID; do
    require_value "${stripe_key}"
    reject_placeholder "${stripe_key}"
  done
  stripe_secret_key="$(env_value STRIPE_SECRET_KEY)"
  stripe_webhook_secret="$(env_value STRIPE_WEBHOOK_SECRET)"
  stripe_launch_price_id="$(env_value STRIPE_LAUNCH_PRICE_ID)"
  stripe_autopilot_price_id="$(env_value STRIPE_AUTOPILOT_PRICE_ID)"
  [[ "${stripe_secret_key}" == sk_* ]] || fail "STRIPE_SECRET_KEY must be a Stripe secret key"
  [[ "${stripe_webhook_secret}" == whsec_* ]] || \
    fail "STRIPE_WEBHOOK_SECRET must be a Stripe webhook signing secret"
  [[ "${stripe_launch_price_id}" == price_* ]] || \
    fail "STRIPE_LAUNCH_PRICE_ID must be a Stripe Price ID"
  [[ "${stripe_autopilot_price_id}" == price_* ]] || \
    fail "STRIPE_AUTOPILOT_PRICE_ID must be a Stripe Price ID"

  # Public Autopilot exposes self-service Meta connection. Provider credentials are
  # encrypted at rest and the OAuth app must be explicitly configured before launch.
  for meta_key in \
    PROVIDER_SECRET_ENCRYPTION_KEY \
    META_OAUTH_APP_ID \
    META_OAUTH_APP_SECRET \
    META_OAUTH_API_VERSION; do
    require_value "${meta_key}"
    reject_placeholder "${meta_key}"
  done
  provider_secret_key="$(env_value PROVIDER_SECRET_ENCRYPTION_KEY)"
  meta_app_id="$(env_value META_OAUTH_APP_ID)"
  meta_app_secret="$(env_value META_OAUTH_APP_SECRET)"
  meta_api_version="$(env_value META_OAUTH_API_VERSION)"
  (( ${#provider_secret_key} >= 40 )) || \
    fail "PROVIDER_SECRET_ENCRYPTION_KEY must be a strong Fernet key"
  [[ "${meta_app_id}" =~ ^[0-9]+$ ]] || fail "META_OAUTH_APP_ID must be a numeric Meta app ID"
  (( ${#meta_app_secret} >= 16 )) || fail "META_OAUTH_APP_SECRET is too short"
  [[ "${meta_api_version}" =~ ^v[0-9]+\.[0-9]+$ ]] || \
    fail "META_OAUTH_API_VERSION must look like v25.0"
elif [[ -n "${public_host}" ]]; then
  fail "PARTIZAN_PUBLIC_HOST must be empty when PARTIZAN_PUBLIC_BASE_URL is empty"
fi

openai_required=false
for provider_key in LLM_PROVIDER SEARCH_PROVIDER CREATIVE_PROVIDER; do
  if [[ "$(env_value "${provider_key}")" == "openai" ]]; then
    openai_required=true
  fi
done
if [[ "${openai_required}" == "true" ]]; then
  require_value OPENAI_API_KEY
  reject_placeholder OPENAI_API_KEY
fi

if [[ "$(env_value CREATIVE_VIDEO_PROVIDER)" == "gemini_omni" ]]; then
  require_value GEMINI_API_KEY
  reject_placeholder GEMINI_API_KEY
fi

compose_file_args=(-f docker-compose.prod.yml)
if [[ -n "${public_base_url}" && "${MANAGED_EDGE}" == "true" ]]; then
  compose_file_args+=(-f docker-compose.edge.yml)
fi
for extra_compose_file in ${EXTRA_COMPOSE_FILES}; do
  compose_file_args+=(-f "${extra_compose_file}")
done

PARTIZAN_ENV_FILE="${ENV_FILE}" \
  docker compose "${compose_file_args[@]}" --env-file "${ENV_FILE}" config --quiet

echo "production host preflight: ok"
