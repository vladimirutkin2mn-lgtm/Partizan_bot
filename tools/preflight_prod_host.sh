#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env.prod}"
REQUIRE_PUBLIC_URL="${PARTIZAN_REQUIRE_PUBLIC_URL:-false}"

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
if [[ "${REQUIRE_PUBLIC_URL}" == "true" && -z "${public_base_url}" ]]; then
  fail "PARTIZAN_PUBLIC_BASE_URL is required for public production smoke"
fi
if [[ -n "${public_base_url}" && ! "${public_base_url}" =~ ^https://[^/?#]+/?$ ]]; then
  fail "PARTIZAN_PUBLIC_BASE_URL must be an HTTPS origin without path/query/fragment"
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

PARTIZAN_ENV_FILE="${ENV_FILE}" \
  docker compose -f docker-compose.prod.yml --env-file "${ENV_FILE}" config --quiet

echo "production host preflight: ok"
