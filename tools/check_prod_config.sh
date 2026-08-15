#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-.env.example}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Environment file not found: ${ENV_FILE}" >&2
  exit 1
fi

PARTIZAN_ENV_FILE="${ENV_FILE}" \
  docker compose -f docker-compose.prod.yml --env-file "${ENV_FILE}" config --quiet

echo "production compose config: ok"
