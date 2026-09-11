#!/usr/bin/env bash
set -euo pipefail

: "${DEPLOY_HOST:?DEPLOY_HOST is required}"
: "${PARTIZAN_PUBLIC_URL:?PARTIZAN_PUBLIC_URL is required}"

public_host="${PARTIZAN_PUBLIC_URL#https://}"
public_host="${public_host%%/*}"
public_host="${public_host%%:*}"

if [[ -z "${public_host}" || ! "${public_host}" =~ ^[A-Za-z0-9.-]+$ ]]; then
  echo "shared-host TLS diagnostics: invalid public hostname" >&2
  exit 1
fi

echo "==> Shared-host TLS diagnostics (read-only)"
echo "public-host=${public_host}"

ssh -o BatchMode=yes "${DEPLOY_HOST}" bash -s -- "${public_host}" <<'REMOTE'
set -u

host="$1"
tls_container_id=""
proxy_kind="unknown"

echo "-- DNS resolution from production host"
resolved_ips="$(getent ahosts "${host}" 2>/dev/null | awk '{print $1}' | sort -u || true)"
if [[ -n "${resolved_ips}" ]]; then
  printf '%s\n' "${resolved_ips}"
else
  echo "dns-resolution=unavailable"
fi

echo "-- TCP/443 listeners on production host"
if command -v ss >/dev/null 2>&1; then
  listeners="$(ss -ltnp 2>/dev/null | awk 'NR == 1 || $4 ~ /:443$/ {print}' || true)"
  if [[ -n "${listeners}" ]]; then
    printf '%s\n' "${listeners}"
  else
    echo "tcp-443-listener=none-visible"
  fi
else
  echo "tcp-443-listener=ss-unavailable"
fi

echo "-- Docker TLS-terminator summary"
if command -v docker >/dev/null 2>&1; then
  docker_rows="$(docker ps --format '{{.ID}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null || true)"
  published_443_count="$(printf '%s\n' "${docker_rows}" | grep -Ec ':443->' || true)"
  echo "docker-published-443-count=${published_443_count}"
  tls_container_id="$(printf '%s\n' "${docker_rows}" | awk -F '\t' '$3 ~ /:443->/ {print $1; exit}' || true)"
  if printf '%s\n' "${docker_rows}" | grep -Eiq 'caddy'; then
    proxy_kind="caddy"
  elif printf '%s\n' "${docker_rows}" | grep -Eiq 'nginx'; then
    proxy_kind="nginx"
  elif printf '%s\n' "${docker_rows}" | grep -Eiq 'traefik'; then
    proxy_kind="traefik"
  elif printf '%s\n' "${docker_rows}" | grep -Eiq 'haproxy'; then
    proxy_kind="haproxy"
  elif printf '%s\n' "${docker_rows}" | grep -Eiq 'envoy'; then
    proxy_kind="envoy"
  fi
  echo "docker-proxy-kind=${proxy_kind}"
else
  echo "docker-published-443-count=unavailable"
  echo "docker-proxy-kind=unavailable"
fi

if [[ "${proxy_kind}" == "caddy" && -n "${tls_container_id}" ]]; then
  echo "-- Caddy target checks (read-only, boolean-only)"
  caddyfile_readable=false
  if docker exec "${tls_container_id}" sh -c 'test -r /etc/caddy/Caddyfile' >/dev/null 2>&1; then
    caddyfile_readable=true
    echo "caddy-config-readable=true"
  else
    echo "caddy-config-readable=false"
  fi

  if [[ "${caddyfile_readable}" == "true" ]] && \
    docker exec "${tls_container_id}" caddy validate \
      --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    echo "caddy-config-validation=ok"
  elif [[ "${caddyfile_readable}" == "true" ]]; then
    echo "caddy-config-validation=failed"
  else
    echo "caddy-config-validation=unavailable"
  fi

  # Read the running config rather than the Caddyfile: routes are imported from
  # a separate directory, so the entry point on its own says nothing about which
  # hosts are actually served.
  if docker exec "${tls_container_id}" sh -c \
    'wget -q -O - http://127.0.0.1:2019/config/apps/http/servers >/dev/null 2>&1'; then
    if docker exec "${tls_container_id}" sh -c \
      'wget -q -O - http://127.0.0.1:2019/config/apps/http/servers 2>/dev/null | grep -Fq -- "\"$1\""' \
      sh "${host}" >/dev/null 2>&1; then
      echo "caddy-target-host-route=present"
    else
      echo "caddy-target-host-route=missing"
    fi
  elif [[ "${caddyfile_readable}" == "true" ]]; then
    if docker exec "${tls_container_id}" sh -c \
      'grep -Fq -- "$1" /etc/caddy/Caddyfile' sh "${host}" >/dev/null 2>&1; then
      echo "caddy-target-host-route=present"
    else
      echo "caddy-target-host-route=missing"
    fi
  else
    echo "caddy-target-host-route=unknown"
  fi

  if docker exec "${tls_container_id}" sh -c \
    'test -d /data/caddy/certificates' >/dev/null 2>&1; then
    if docker exec "${tls_container_id}" sh -c \
      'find /data/caddy/certificates -type f -path "*/$1/*" -print -quit 2>/dev/null | grep -q .' \
      sh "${host}" >/dev/null 2>&1; then
      echo "caddy-target-certificate-storage=present"
    else
      echo "caddy-target-certificate-storage=missing"
    fi
  else
    echo "caddy-target-certificate-storage=unknown"
  fi
else
  echo "-- Caddy target checks"
  echo "caddy-config-readable=unavailable"
  echo "caddy-config-validation=unavailable"
  echo "caddy-target-host-route=unknown"
  echo "caddy-target-certificate-storage=unknown"
fi

echo "-- Local loopback SNI probe"
loopback_tls_ok=false
if command -v curl >/dev/null 2>&1; then
  set +e
  loopback_result="$(curl --silent --show-error --max-time 10 \
    --resolve "${host}:443:127.0.0.1" \
    --output /dev/null \
    --write-out 'http=%{http_code} remote=%{remote_ip} verify=%{ssl_verify_result}' \
    "https://${host}/health/live" 2>&1)"
  loopback_rc=$?
  set -e
  echo "loopback-curl-rc=${loopback_rc} ${loopback_result}"
  if [[ ${loopback_rc} -eq 0 ]]; then
    loopback_tls_ok=true
  fi
else
  echo "loopback-curl=curl-unavailable"
fi

echo "-- Local SNI certificate summary"
if command -v openssl >/dev/null 2>&1; then
  cert_file="$(mktemp)"
  openssl_log="$(mktemp)"
  set +e
  timeout 10 openssl s_client \
    -connect 127.0.0.1:443 \
    -servername "${host}" \
    -showcerts </dev/null >"${openssl_log}" 2>&1
  openssl_rc=$?
  set -e
  awk '/-----BEGIN CERTIFICATE-----/{capture=1} capture{print} /-----END CERTIFICATE-----/{exit}' \
    "${openssl_log}" >"${cert_file}"
  if [[ -s "${cert_file}" ]]; then
    openssl x509 -in "${cert_file}" -noout -subject -issuer -dates -ext subjectAltName 2>/dev/null || true
  else
    echo "local-sni-certificate=none openssl-rc=${openssl_rc}"
    tail -n 4 "${openssl_log}" | sed 's/^/openssl: /'
  fi
  rm -f "${cert_file}" "${openssl_log}"
else
  echo "local-sni-certificate=openssl-unavailable"
fi

echo "-- Public DNS-target TLS probes from production host"
if command -v curl >/dev/null 2>&1 && [[ -n "${resolved_ips}" ]]; then
  while IFS= read -r ip; do
    [[ -n "${ip}" ]] || continue
    case "${ip}" in
      *:*) continue ;;
    esac
    set +e
    public_result="$(curl --silent --show-error --max-time 10 \
      --resolve "${host}:443:${ip}" \
      --output /dev/null \
      --write-out 'http=%{http_code} remote=%{remote_ip} verify=%{ssl_verify_result}' \
      "https://${host}/health/live" 2>&1)"
    public_rc=$?
    set -e
    echo "dns-target=${ip} curl-rc=${public_rc} ${public_result}"
  done <<< "${resolved_ips}"
else
  echo "dns-target-probe=unavailable"
fi

if [[ "${loopback_tls_ok}" == "true" ]]; then
  echo "classification=local_tls_handshake_ok_check_proxy_route_or_external_dns"
else
  echo "classification=local_sni_tls_failed_check_shared_proxy_certificate_and_host_route"
fi

echo "shared-host TLS diagnostics are read-only; no proxy reload/restart was attempted"
REMOTE
