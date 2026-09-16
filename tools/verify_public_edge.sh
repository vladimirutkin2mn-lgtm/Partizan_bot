#!/usr/bin/env bash
set -euo pipefail

# Public reachability check for the production edge.
#
# Internal `compose exec` probes stay green while the public hostname serves 502: they
# reach the API directly and never traverse the proxy that terminates TLS. Public
# reachability is therefore the only evidence that a production mutation left the site
# serving, so every mutation path finishes here and the edge watchdog polls it.

PARTIZAN_PUBLIC_URL="${PARTIZAN_PUBLIC_URL:?Set PARTIZAN_PUBLIC_URL (https://host)}"
ATTEMPTS="${PARTIZAN_PUBLIC_EDGE_ATTEMPTS:-5}"
DELAY_SECONDS="${PARTIZAN_PUBLIC_EDGE_DELAY_SECONDS:-10}"

if [[ "${PARTIZAN_PUBLIC_URL}" != https://* ]]; then
  echo "Refusing public edge check: PARTIZAN_PUBLIC_URL must use https://" >&2
  exit 1
fi

if [[ ! "${ATTEMPTS}" =~ ^[1-9][0-9]*$ || ! "${DELAY_SECONDS}" =~ ^[0-9]+$ ]]; then
  echo "Refusing public edge check: attempts and delay must be numeric" >&2
  exit 1
fi

base="${PARTIZAN_PUBLIC_URL%/}"

for attempt in $(seq 1 "${ATTEMPTS}"); do
  unhealthy_path=""
  for path in /health/live /health/ready; do
    status="$(curl --silent --show-error --output /dev/null --max-time 10 \
      --write-out '%{http_code}' "${base}${path}" 2>/dev/null || true)"
    if [[ "${status}" != "200" ]]; then
      echo "public edge check: ${path} returned ${status:-000} (attempt ${attempt}/${ATTEMPTS})" >&2
      unhealthy_path="${path}"
      break
    fi
  done

  if [[ -z "${unhealthy_path}" ]]; then
    echo "public-edge=healthy"
    exit 0
  fi

  if (( attempt < ATTEMPTS )); then
    sleep "${DELAY_SECONDS}"
  fi
done

echo "public-edge=unreachable" >&2
exit 1
