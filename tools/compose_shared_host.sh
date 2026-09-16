#!/usr/bin/env bash
# Compose wrapper for a production host.
#
# Guarantees every operation — ad-hoc or automated — uses exactly the file set the deploy
# script used. Forgetting the shared-host overlay recreates the API without the shared
# proxy network, so the public hostname serves 502 until someone notices, while every
# internal `compose exec` probe still reports a healthy API.
#
# Also keeps the production environment file's path out of ad-hoc command lines, so it
# stays out of shell history, process listings and operator transcripts.
#
# The overlay is selected from the host's own configuration instead of from the caller:
# the overlay requires PARTIZAN_EDGE_NETWORK, so that variable's presence in .env.prod is
# the exact signal that this host is routed by a proxy it does not own.
#
# Usage, from anywhere:
#   bash /opt/partizan_bot/tools/compose_shared_host.sh ps
#   bash /opt/partizan_bot/tools/compose_shared_host.sh logs --tail=50 api
#
# When driving this over `ssh`, remember every compose subcommand that reads stdin needs
# `-T` and `</dev/null`, or it consumes the rest of the piped script and appears to hang.
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=".env.prod"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Refusing compose invocation: ${ENV_FILE} is missing" >&2
  exit 1
fi

compose_files=(-f docker-compose.prod.yml)
if grep -qE '^PARTIZAN_EDGE_NETWORK=[^[:space:]]' "${ENV_FILE}"; then
  compose_files+=(-f docker-compose.shared-host.yml)
fi

exec docker compose "${compose_files[@]}" --env-file "${ENV_FILE}" "$@"
