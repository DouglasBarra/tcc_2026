#!/usr/bin/env bash
# Roda measure.cjs num container node efêmero (nada e instalado no host).
# Ex.: ./scripts/measure.sh --db tcc_lab --sql "SELECT COUNT(*) FROM dbo.sanidade WHERE cliente=42" --runs 5 --cold
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm --network infra_default --env-file .env -e DEBUG_MSGS="${DEBUG_MSGS:-}" \
  -v "$PWD/scripts:/app:ro" -w /tmp node:22-slim sh -c \
  'npm i tedious --silent --no-audit --no-fund >/dev/null 2>&1 && NODE_PATH=/tmp/node_modules node /app/measure.cjs "$@"' _ "$@"
