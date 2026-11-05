#!/usr/bin/env bash
# Sobe o SQL Server, espera ficar saudavel e aplica sql/00-init.sql (idempotente).
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "Falta .env (cp .env.example .env)"; exit 1; }
start=$(date +%s)
docker compose up -d
echo "Aguardando healthcheck..."
for i in $(seq 1 90); do
  st=$(docker inspect -f '{{.State.Health.Status}}' tcc-sqlserver 2>/dev/null || echo starting)
  [ "$st" = "healthy" ] && break
  sleep 5
done
[ "$st" = "healthy" ] || { echo "Nao ficou saudavel (status=$st)"; docker compose logs --tail 50; exit 1; }
echo "Saudavel em $(( $(date +%s) - start ))s. Aplicando init..."
./scripts/sqlcmd.sh -i /sql/00-init.sql
echo "Pronto: localhost:${MSSQL_PORT:-1433}"
