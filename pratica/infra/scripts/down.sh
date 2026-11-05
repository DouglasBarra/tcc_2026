#!/usr/bin/env bash
# Para o container PRESERVANDO o volume de dados. Use --destroy para apagar o volume (irreversivel).
# Primeiro faz SHUTDOWN T-SQL (desligamento limpo); so o SIGTERM do 'docker stop' nao
# desligou o SQL Server a tempo neste ambiente (Rosetta) e o container acabava em SIGKILL.
set -euo pipefail
cd "$(dirname "$0")/.."
if docker inspect -f '{{.State.Running}}' tcc-sqlserver 2>/dev/null | grep -q true; then
  ./scripts/sqlcmd.sh -Q "SHUTDOWN" >/dev/null 2>&1 || true
  for i in $(seq 1 30); do
    [ "$(docker inspect -f '{{.State.Running}}' tcc-sqlserver)" = "false" ] && break; sleep 1
  done
fi
if [ "${1:-}" = "--destroy" ]; then
  read -r -p "Isto APAGA o volume tcc_sqlserver_data. Digite 'sim': " r
  [ "$r" = "sim" ] && docker compose down -v || echo "Cancelado."
else
  docker compose stop
fi
