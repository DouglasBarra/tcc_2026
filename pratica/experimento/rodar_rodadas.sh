#!/usr/bin/env bash
# Rodadas completas encadeadas (sem regerar planos): compat 160 (quente+frio) e depois compat 100 (so quente).
# Uso: ./rodar_rodadas.sh <prefixo> [argumentos extras do coletar.py, ex.: --sem-repetir-por-deriva]
set -u
cd "$(dirname "$0")/../nucleo"
PY=.venv/bin/python
PRE="${1:?informe o prefixo da rodada, ex.: r1}"; shift
export PYTHONUNBUFFERED=1
echo "[$(date +%H:%M:%S)] rodada ${PRE}-compat160"
$PY ../experimento/coletar.py --rodada "${PRE}-compat160" --compat 160 "$@" || echo "FALHA rodada 160"
echo "[$(date +%H:%M:%S)] rodada ${PRE}-compat100 (sensibilidade, so quente)"
$PY ../experimento/coletar.py --rodada "${PRE}-compat100" --compat 100 --regimes quente "$@" || echo "FALHA rodada 100"
echo "[$(date +%H:%M:%S)] FIM"
