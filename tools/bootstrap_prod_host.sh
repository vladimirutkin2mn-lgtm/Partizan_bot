#!/usr/bin/env bash
set -euo pipefail

DEPLOY_PATH="${1:?Usage: bootstrap_prod_host.sh /absolute/partizan/path}"
PUBLIC_BASE_URL="${PARTIZAN_PUBLIC_BASE_URL:-}"

fail() {
  echo "production bootstrap: $*" >&2
  exit 1
}

[[ "${DEPLOY_PATH}" == /* ]] || fail "deploy path must be absolute"
command -v openssl >/dev/null 2>&1 || fail "openssl is required to generate deployment secrets"

if [[ -n "${PUBLIC_BASE_URL}" && ! "${PUBLIC_BASE_URL}" =~ ^https://[^/?#]+/?$ ]]; then
  fail "PARTIZAN_PUBLIC_BASE_URL must be an HTTPS origin without path/query/fragment"
fi
PUBLIC_BASE_URL="${PUBLIC_BASE_URL%/}"
PUBLIC_HOST=""
if [[ -n "${PUBLIC_BASE_URL}" ]]; then
  PUBLIC_HOST="${PUBLIC_BASE_URL#https://}"
fi

mkdir -p "${DEPLOY_PATH}"
chmod 750 "${DEPLOY_PATH}"
ENV_FILE="${DEPLOY_PATH}/.env.prod"
[[ ! -e "${ENV_FILE}" ]] || fail "${ENV_FILE} already exists; refusing to overwrite deployment secrets"

umask 077
POSTGRES_PASSWORD="$(openssl rand -hex 32)"
OPERATOR_API_KEY="$(openssl rand -hex 32)"

cat > "${ENV_FILE}" <<EOF
APP_ENV=production
APP_LOG_LEVEL=INFO
RUNTIME_STORAGE=database

# Core research providers. Keep mock until deployment-specific live credentials are configured.
LLM_PROVIDER=mock
LLM_MODEL=gpt-5.6
SEARCH_PROVIDER=mock
SEARCH_MODEL=gpt-5.6-terra
OPENAI_API_KEY=

# Optional creative providers remain fail-closed until explicitly configured.
CREATIVE_PROVIDER=unavailable
CREATIVE_IMAGE_MODEL=gpt-image-2
CREATIVE_IMAGE_QUALITY=medium
GEMINI_API_KEY=
CREATIVE_VIDEO_PROVIDER=unavailable
CREATIVE_VIDEO_MODEL=gemini-omni-flash-preview
EXECUTION_PROVIDER=mock

OPERATOR_AUTH_REQUIRED=true
OPERATOR_API_KEY=${OPERATOR_API_KEY}
PARTIZAN_PUBLIC_BASE_URL=${PUBLIC_BASE_URL}
PARTIZAN_PUBLIC_HOST=${PUBLIC_HOST}

# Customer billing. A public launch preflight will fail until the first three values
# are explicitly configured. Keep secrets only in this host-local 0600 file.
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_LAUNCH_PRICE_ID=
STRIPE_AUTOPILOT_PRICE_ID=
PARTIZAN_LAUNCH_PRICE_USD=49
PARTIZAN_AUTOPILOT_PRICE_USD=149
PARTIZAN_MANAGED_SPEND_FEE_PCT=10

# Owned SMTP sender is intentionally disabled until explicitly configured.
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=
SMTP_FROM_NAME=
SMTP_REPLY_TO=
SMTP_STARTTLS=true

POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
CONTAINER_DATABASE_URL=postgresql+asyncpg://partizan:${POSTGRES_PASSWORD}@postgres:5432/partizan
PARTIZAN_API_PORT=8000
PAID_CONTROL_INTERVAL_SECONDS=60
AUTONOMOUS_GROWTH_INTERVAL_SECONDS=300
EOF

chmod 600 "${ENV_FILE}"
unset POSTGRES_PASSWORD OPERATOR_API_KEY

echo "production bootstrap: created ${ENV_FILE} with generated deployment-only secrets"
echo "production bootstrap: live providers, Stripe billing and public URL still require explicit configuration where left blank/mock"
