#!/usr/bin/env bash
# Encadeia: planos do catalogo e dos controles -> rodada R1 (compat 160) -> sensibilidade (compat 100, so quente).
# Uso: ./rodar_r1.sh [prefixo]    (prefixo padrao: r1). Registra tudo em resultados/<prefixo>-*/coleta.log
set -u
cd "$(dirname "$0")/../nucleo"
PY=.venv/bin/python
PRE="${1:-r1}"
export PYTHONUNBUFFERED=1
echo "[$(date +%H:%M:%S)] planos do catalogo"
$PY ../experimento/salvar_planos.py --compat 160 || echo "FALHA planos catalogo"
echo "[$(date +%H:%M:%S)] planos dos controles limpos"
$PY ../experimento/salvar_planos.py --compat 160 --catalogo ../experimento/controles_limpos.toml --saida ../experimento/planos_controles || echo "FALHA planos controles"
echo "[$(date +%H:%M:%S)] rodada ${PRE}-compat160"
$PY ../experimento/coletar.py --rodada "${PRE}-compat160" --compat 160 || echo "FALHA rodada 160"
echo "[$(date +%H:%M:%S)] rodada ${PRE}-compat100 (sensibilidade, so quente)"
$PY ../experimento/coletar.py --rodada "${PRE}-compat100" --compat 100 --regimes quente || echo "FALHA rodada 100"
echo "[$(date +%H:%M:%S)] FIM"
