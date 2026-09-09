#!/usr/bin/env bash
set -euo pipefail

# Flip the Partizan-funded Growth Balance rail from `unavailable` to `stripe_issuing`
# on a production host. Run this from the release directory, on the host that owns
# .env.prod. Secrets are read from an interactive prompt so they never reach argv,
# shell history or process listings.
#
# This script only changes configuration. It does not enable Stripe Issuing, create a
# cardholder or pre-fund Issuing liquidity; see docs/GROWTH_BALANCE_ISSUING.md.

ENV_FILE="${1:-.env.prod}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  echo "growth balance rail: $*" >&2
  exit 1
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

require_existing() {
  local key="$1"
  [[ -n "$(env_value "${key}")" ]] || \
    fail "${key} must already be configured in ${ENV_FILE} before the funded rail is enabled"
}

# Replace the key if it exists, append it otherwise. The value travels through the
# environment instead of argv so it never shows up in `ps`.
set_key() {
  local key="$1"
  local value="$2"
  SET_KEY="${key}" SET_VALUE="${value}" awk '
    BEGIN { key = ENVIRON["SET_KEY"]; value = ENVIRON["SET_VALUE"]; written = 0 }
    $0 ~ "^" key "=" {
      if (!written) { print key "=" value; written = 1 }
      next
    }
    { print }
    END { if (!written) print key "=" value }
  ' "${WORK_FILE}" > "${WORK_FILE}.next"
  mv "${WORK_FILE}.next" "${WORK_FILE}"
}

prompt_value() {
  # prompt_value <target-var> <label> <expected-prefix> <silent:true|false>
  local target="$1" label="$2" prefix="$3" silent="$4"
  local value=""
  if [[ "${silent}" == "true" ]]; then
    read -rsp "${label}: " value
    echo
  else
    read -rp "${label}: " value
  fi
  [[ -n "${value}" ]] || fail "${label} must not be empty"
  [[ "${value}" == ${prefix}* ]] || fail "${label} must start with ${prefix}"
  [[ "${value}" != *[[:space:]]* ]] || fail "${label} must not contain whitespace"
  printf -v "${target}" '%s' "${value}"
}

[[ -f "${ENV_FILE}" ]] || fail "environment file not found: ${ENV_FILE}"
[[ -t 0 ]] || fail "run this from an interactive shell so secrets stay out of argv and history"

mode="$(stat -c '%a' "${ENV_FILE}" 2>/dev/null || stat -f '%Lp' "${ENV_FILE}" 2>/dev/null || true)"
[[ "${mode}" == "600" ]] || fail "${ENV_FILE} permissions must be 600 (current: ${mode:-unknown})"

current_provider="$(env_value GROWTH_BALANCE_SETTLEMENT_PROVIDER)"
if [[ "${current_provider}" == "stripe_issuing" ]]; then
  echo "growth balance rail: already set to stripe_issuing; re-running only refreshes Issuing values"
fi

# Customer money must never be accepted before the surrounding billing stack is live.
require_existing PARTIZAN_PUBLIC_BASE_URL
require_existing STRIPE_SECRET_KEY
require_existing STRIPE_WEBHOOK_SECRET
require_existing STRIPE_LAUNCH_PRICE_ID
require_existing STRIPE_AUTOPILOT_PRICE_ID

echo "growth balance rail: enabling stripe_issuing in ${ENV_FILE}"
echo "growth balance rail: paste Stripe Issuing values (input for secrets stays hidden)"

cardholder_id=""
authorization_secret=""
events_secret=""
prompt_value cardholder_id "Issuing Cardholder ID (ich_...)" "ich_" false
prompt_value authorization_secret "Authorization webhook signing secret (whsec_...)" "whsec_" true
prompt_value events_secret "Issuing events webhook signing secret (whsec_...)" "whsec_" true

[[ "${authorization_secret}" != "${events_secret}" ]] || \
  fail "the authorization and events webhook endpoints must be separate Stripe endpoints"

api_version="$(env_value STRIPE_ISSUING_WEBHOOK_API_VERSION)"
api_version="${api_version:-2025-03-31.basil}"

umask 077
WORK_FILE="$(mktemp "${ENV_FILE}.new.XXXXXX")"
BACKUP_FILE="${ENV_FILE}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
trap 'rm -f "${WORK_FILE}" "${WORK_FILE}.next"' EXIT

cp "${ENV_FILE}" "${WORK_FILE}"
set_key GROWTH_BALANCE_SETTLEMENT_PROVIDER stripe_issuing
set_key STRIPE_ISSUING_CARDHOLDER_ID "${cardholder_id}"
set_key STRIPE_ISSUING_CURRENCY usd
set_key STRIPE_ISSUING_AUTHORIZATION_WEBHOOK_SECRET "${authorization_secret}"
set_key STRIPE_ISSUING_EVENTS_WEBHOOK_SECRET "${events_secret}"
set_key STRIPE_ISSUING_WEBHOOK_API_VERSION "${api_version}"

cp "${ENV_FILE}" "${BACKUP_FILE}"
chmod 600 "${BACKUP_FILE}"
mv "${WORK_FILE}" "${ENV_FILE}"
chmod 600 "${ENV_FILE}"
trap - EXIT

echo "growth balance rail: previous configuration saved to ${BACKUP_FILE} (mode 600)"
echo "growth balance rail: running production preflight"
if ! PARTIZAN_REQUIRE_PUBLIC_URL=true bash "${REPO_ROOT}/tools/preflight_prod_host.sh" "${ENV_FILE}"; then
  cp "${BACKUP_FILE}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
  fail "preflight rejected the funded rail; ${ENV_FILE} was restored from ${BACKUP_FILE}"
fi

cat <<'NEXT'
growth balance rail: configuration accepted.

Still required before a customer can fund a Growth Balance:
  1. Deploy this release so the API reads the new configuration.
  2. Verify the live money rail from inside the API container:
       python -m app.stripe_readiness --required-liquidity-usd=<committed acquisition capacity>
     A non-zero exit means Stripe Issuing is not usable yet and top-ups stay blocked.
  3. Confirm both Stripe webhook endpoints are delivering:
       POST /v1/billing/stripe/issuing-authorizations
       POST /v1/billing/stripe/issuing-events
  4. Bind the Partizan project card to the exact Meta ad account
     (docs/GROWTH_BALANCE_ISSUING.md, step 7).

To roll back, restore the printed backup file and redeploy.
NEXT
