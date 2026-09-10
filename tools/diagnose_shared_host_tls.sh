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

echo "-- Docker containers publishing or likely terminating TLS"
if command -v docker >/dev/null 2>&1; then
  docker_rows="$(docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}' 2>/dev/null || true)"
  matching_rows="$(printf '%s\n' "${docker_rows}" | grep -Ei '(:443->|caddy|nginx|traefik|haproxy|envoy)' || true)"
  if [[ -n "${matching_rows}" ]]; then
    printf '%s\n' "${matching_rows}"
  else
    echo "docker-tls-candidate=none-visible"
  fi
else
  echo "docker-tls-candidate=docker-unavailable"
fi

echo "-- Local loopback SNI probe"
loopback_ok=false
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
  if [[ ${loopback_rc} -eq 0 && "${loopback_result}" == *"http=200"* ]]; then
    loopback_ok=true
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

if [[ "${loopback_ok}" == "true" ]]; then
  echo "classification=local_tls_ok_check_dns_or_upstream_path"
else
  echo "classification=local_sni_failed_check_shared_proxy_certificate_and_host_route"
fi

echo "shared-host TLS diagnostics are read-only; no proxy reload/restart was attempted"
REMOTE
