#!/usr/bin/env bash
# Executa sqlcmd dentro do container como SA. Exemplos:
#   ./scripts/sqlcmd.sh -Q "SELECT @@VERSION"
#   ./scripts/sqlcmd.sh -d tcc_lab -i /sql/arquivo.sql     (a pasta sql/ e montada em /sql)
#   echo "SELECT 1" | ./scripts/sqlcmd.sh -d tcc_lab
set -euo pipefail
cd "$(dirname "$0")/.."
# -C confia no certificado autoassinado; a senha vem do env do container (nao aparece no ps do host).
exec docker exec -i tcc-sqlserver bash -c \
  '/opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P "$MSSQL_SA_PASSWORD" "$@"' _ "$@"
