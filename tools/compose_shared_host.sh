#!/usr/bin/env bash
# Compose wrapper for a shared-host deployment.
#
# Guarantees ad-hoc operations use exactly the file set the deploy script used — forgetting
# the shared-host overlay detaches the API from the proxy network and silently breaks public
# routing on the next `up`.
#
# Also keeps the production environment file's path out of ad-hoc command lines, so it stays
# out of shell history, process listings and operator transcripts.
#
# Usage, from anywhere:
#   bash /opt/partizan_bot/tools/compose_shared_host.sh ps
#   bash /opt/partizan_bot/tools/compose_shared_host.sh logs --tail=50 api
#
# When driving this over `ssh`, remember every compose subcommand that reads stdin needs
# `-T` and `</dev/null`, or it consumes the rest of the piped script and appears to hang.
set -euo pipefail

cd "$(dirname "$0")/.."

exec docker compose \
  -f docker-compose.prod.yml \
  -f docker-compose.shared-host.yml \
  --env-file .env.prod \
  "$@"
