#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env.prod}"
REQUIRE_PUBLIC_URL="${PARTIZAN_REQUIRE_PUBLIC_URL:-false}"
# Mirrors tools/deploy_prod_remote.sh so the validated compose set is the deployed one.
MANAGED_EDGE="${PARTIZAN_MANAGED_EDGE:-true}"
EXTRA_COMPOSE_FILES="${PARTIZAN_EXTRA_COMPOSE_FILES:-}"
CONFIG_ERRORS=()

fail() {
  echo "production preflight: $*" >&2
  exit 1
}

config_error() {
  CONFIG_ERRORS+=("$*")
}

report_config_errors() {
  if (( ${#CONFIG_ERRORS[@]} == 0 )); then
    return
  fi
  echo "production preflight failed with ${#CONFIG_ERRORS[@]} configuration error(s):" >&2
  for error in "${CONFIG_ERRORS[@]}"; do
    echo " - ${error}" >&2
  done
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
  if [[ -z "${value}" ]]; then
    config_error "${key} is required in ${ENV_FILE}"
  fi
}

reject_placeholder() {
  local key="$1"
  local value
  value="$(env_value "${key}")"
  [[ -n "${value}" ]] || return
  case "${value,,}" in
    partizan|change-me|changeme|replace-me|replace_me|example|secret|password)
      config_error "${key} still uses a default placeholder value"
      ;;
  esac
  if [[ "${value}" == *"<"* || "${value}" == *">"* ]]; then
    config_error "${key} still contains a placeholder"
  fi
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
if [[ -n "${postgres_password}" ]] && (( ${#postgres_password} < 24 )); then
  config_error "POSTGRES_PASSWORD must be at least 24 characters"
fi

require_value OPERATOR_API_KEY
reject_placeholder OPERATOR_API_KEY
operator_key="$(env_value OPERATOR_API_KEY)"
if [[ -n "${operator_key}" ]] && (( ${#operator_key} < 32 )); then
  config_error "OPERATOR_API_KEY must be at least 32 characters"
fi

app_env="$(env_value APP_ENV)"
if [[ -n "${app_env}" && "${app_env}" != "production" && "${app_env}" != "prod" ]]; then
  config_error "APP_ENV must be production/prod when set in ${ENV_FILE}"
fi

runtime_storage="$(env_value RUNTIME_STORAGE)"
if [[ -n "${runtime_storage}" && "${runtime_storage}" != "database" ]]; then
  config_error "RUNTIME_STORAGE must be database when set in ${ENV_FILE}"
fi

require_value CONTAINER_DATABASE_URL
container_database_url="$(env_value CONTAINER_DATABASE_URL)"
if [[ -n "${container_database_url}" ]]; then
  [[ "${container_database_url}" == postgresql+asyncpg://partizan:* ]] || \
    config_error "CONTAINER_DATABASE_URL must use the partizan asyncpg production DSN"
  [[ "${container_database_url}" == *@postgres:5432/partizan* ]] || \
    config_error "CONTAINER_DATABASE_URL must target the internal postgres service"
fi

public_base_url="$(env_value PARTIZAN_PUBLIC_BASE_URL)"
public_host="$(env_value PARTIZAN_PUBLIC_HOST)"
if [[ "${REQUIRE_PUBLIC_URL}" == "true" && -z "${public_base_url}" ]]; then
  config_error "PARTIZAN_PUBLIC_BASE_URL is required for public production smoke"
fi
if [[ -n "${public_base_url}" ]]; then
  [[ "${public_base_url}" =~ ^https://[^/?#]+/?$ ]] || \
    config_error "PARTIZAN_PUBLIC_BASE_URL must be an HTTPS origin without path/query/fragment"
  expected_public_host="${public_base_url#https://}"
  expected_public_host="${expected_public_host%/}"
  if [[ -z "${public_host}" ]]; then
    config_error "PARTIZAN_PUBLIC_HOST is required when public base URL is set"
  else
    [[ "${public_host}" == "${expected_public_host}" ]] || \
      config_error "PARTIZAN_PUBLIC_HOST must exactly match the hostname in PARTIZAN_PUBLIC_BASE_URL"
    [[ "${public_host}" =~ ^[A-Za-z0-9.-]+$ ]] || \
      config_error "PARTIZAN_PUBLIC_HOST must be a DNS hostname without scheme, port or path"
  fi
  if [[ "${MANAGED_EDGE}" == "true" && ( ! -f Caddyfile.prod || ! -f docker-compose.edge.yml ) ]]; then
    config_error "public HTTPS edge files are missing from the release"
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
  if [[ -n "${stripe_secret_key}" && "${stripe_secret_key}" != sk_* ]]; then
    config_error "STRIPE_SECRET_KEY must be a Stripe secret key"
  fi
  if [[ -n "${stripe_webhook_secret}" && "${stripe_webhook_secret}" != whsec_* ]]; then
    config_error "STRIPE_WEBHOOK_SECRET must be a Stripe webhook signing secret"
  fi
  if [[ -n "${stripe_launch_price_id}" && "${stripe_launch_price_id}" != price_* ]]; then
    config_error "STRIPE_LAUNCH_PRICE_ID must be a Stripe Price ID"
  fi
  if [[ -n "${stripe_autopilot_price_id}" && "${stripe_autopilot_price_id}" != price_* ]]; then
    config_error "STRIPE_AUTOPILOT_PRICE_ID must be a Stripe Price ID"
  fi

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
  if [[ -n "${provider_secret_key}" ]] && (( ${#provider_secret_key} < 40 )); then
    config_error "PROVIDER_SECRET_ENCRYPTION_KEY must be a strong Fernet key"
  fi
  if [[ -n "${meta_app_id}" && ! "${meta_app_id}" =~ ^[0-9]+$ ]]; then
    config_error "META_OAUTH_APP_ID must be a numeric Meta app ID"
  fi
  if [[ -n "${meta_app_secret}" ]] && (( ${#meta_app_secret} < 16 )); then
    config_error "META_OAUTH_APP_SECRET is too short"
  fi
  if [[ -n "${meta_api_version}" && ! "${meta_api_version}" =~ ^v[0-9]+\.[0-9]+$ ]]; then
    config_error "META_OAUTH_API_VERSION must look like v25.0"
  fi
elif [[ -n "${public_host}" ]]; then
  config_error "PARTIZAN_PUBLIC_HOST must be empty when PARTIZAN_PUBLIC_BASE_URL is empty"
fi

settlement_provider="$(env_value GROWTH_BALANCE_SETTLEMENT_PROVIDER)"
settlement_provider="${settlement_provider:-unavailable}"
if [[ "${settlement_provider}" != "unavailable" && "${settlement_provider}" != "stripe_issuing" ]]; then
  config_error "GROWTH_BALANCE_SETTLEMENT_PROVIDER must be unavailable or stripe_issuing"
fi
if [[ "${settlement_provider}" == "stripe_issuing" ]]; then
  if [[ -z "${public_base_url}" ]]; then
    config_error "PARTIZAN_PUBLIC_BASE_URL is required for Stripe Issuing authorization webhooks"
  fi
  for issuing_key in \
    STRIPE_SECRET_KEY \
    STRIPE_ISSUING_CARDHOLDER_ID \
    STRIPE_ISSUING_AUTHORIZATION_WEBHOOK_SECRET \
    STRIPE_ISSUING_EVENTS_WEBHOOK_SECRET \
    STRIPE_ISSUING_WEBHOOK_API_VERSION; do
    require_value "${issuing_key}"
    reject_placeholder "${issuing_key}"
  done
  issuing_cardholder_id="$(env_value STRIPE_ISSUING_CARDHOLDER_ID)"
  issuing_currency="$(env_value STRIPE_ISSUING_CURRENCY)"
  issuing_auth_webhook_secret="$(env_value STRIPE_ISSUING_AUTHORIZATION_WEBHOOK_SECRET)"
  issuing_events_webhook_secret="$(env_value STRIPE_ISSUING_EVENTS_WEBHOOK_SECRET)"
  issuing_webhook_api_version="$(env_value STRIPE_ISSUING_WEBHOOK_API_VERSION)"
  if [[ -n "${issuing_cardholder_id}" && "${issuing_cardholder_id}" != ich_* ]]; then
    config_error "STRIPE_ISSUING_CARDHOLDER_ID must be a Stripe Issuing Cardholder ID"
  fi
  if [[ -n "${issuing_currency}" && "${issuing_currency}" != "usd" ]]; then
    config_error "STRIPE_ISSUING_CURRENCY must currently be usd"
  fi
  if [[ -n "${issuing_auth_webhook_secret}" && "${issuing_auth_webhook_secret}" != whsec_* ]]; then
    config_error "STRIPE_ISSUING_AUTHORIZATION_WEBHOOK_SECRET must be a Stripe webhook signing secret"
  fi
  if [[ -n "${issuing_events_webhook_secret}" && "${issuing_events_webhook_secret}" != whsec_* ]]; then
    config_error "STRIPE_ISSUING_EVENTS_WEBHOOK_SECRET must be a Stripe webhook signing secret"
  fi
  if [[ -n "${issuing_webhook_api_version}" && ! "${issuing_webhook_api_version}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\.[A-Za-z0-9_-]+$ ]]; then
    config_error "STRIPE_ISSUING_WEBHOOK_API_VERSION must be an explicit Stripe API version"
  fi
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

report_config_errors

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
