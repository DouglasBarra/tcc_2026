#!/usr/bin/env bash
# TCC Lab: sobe a API (FastAPI, 127.0.0.1:8000) e o frontend de desenvolvimento (Vite, 127.0.0.1:5173).
#
#   ./run.sh               desenvolvimento: API na 8000 + Vite na 5173 (abra http://127.0.0.1:5173)
#   ./run.sh --producao    compila o frontend e serve tudo pela API (http://127.0.0.1:8000)
#
# Variaveis (todas opcionais):
#   TCCLAB_RESULTADOS   pasta com <rodada>/tcclab.sqlite   (padrao: ../experimento/resultados)
#   TCCLAB_AO_VIVO=1    habilita os caminhos que falam com o SQL Server (PADRAO: DESLIGADO)
#
# Esta interface le SQLite em modo somente leitura. Sem TCCLAB_AO_VIVO=1 ela NUNCA conecta ao SQL Server, entao e
# seguro rodar enquanto uma coleta (experimento/coletar.py) usa o banco com exclusividade.
set -euo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
PY="$AQUI/.venv/bin/python"
MODO="${1:-}"

if [ ! -x "$PY" ]; then
  echo "Falta o ambiente Python. Crie com:" >&2
  echo "  cd \"$AQUI\" && /opt/homebrew/bin/python3.14 -m venv .venv && .venv/bin/pip install -e ../nucleo && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
if [ ! -d "$AQUI/frontend/node_modules" ]; then
  echo "Instalando dependencias do frontend (npm ci)..." >&2
  (cd "$AQUI/frontend" && npm ci)
fi

if [ "${TCCLAB_AO_VIVO:-0}" = "1" ]; then
  echo "ATENCAO: TCCLAB_AO_VIVO=1 habilita conexao ao SQL Server. NAO use durante uma coleta." >&2
fi

if [ "$MODO" = "--producao" ]; then
  (cd "$AQUI/frontend" && npm run build)
  cd "$AQUI/backend"
  exec "$PY" -m uvicorn tcclab_api.main:app --host 127.0.0.1 --port 8000
fi

cd "$AQUI/backend"
"$PY" -m uvicorn tcclab_api.main:app --host 127.0.0.1 --port 8000 &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT INT TERM
cd "$AQUI/frontend"
npm run dev
