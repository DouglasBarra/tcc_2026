#!/usr/bin/env bash
# Executado depois da R2 (banco livre): (1) custo de escrita corrigido, (2) iteracao 2 pos-hoc de S2/S3,
# (3) verificacao no banco da equivalencia das reescritas geradas pelo modulo de prescricao (no clone so_exp).
set -u
cd "$(dirname "$0")/../nucleo"
PY=.venv/bin/python
export PYTHONUNBUFFERED=1
echo "[$(date +%H:%M:%S)] escrita corrigida"
$PY ../experimento/escrita_rodada.py --rodada escrita-corrigida --de-rodada r1-compat160 || echo "FALHA escrita"
echo "[$(date +%H:%M:%S)] iteracao 2 (pos-hoc)"
$PY ../experimento/iteracao2.py --rodada iteracao2 || echo "FALHA iteracao2"
echo "[$(date +%H:%M:%S)] verificacao das reescritas geradas"
$PY ../experimento/avaliar_prescricao.py --verificar-no-banco --banco so_exp || echo "FALHA verificacao"
echo "[$(date +%H:%M:%S)] POS-R2 FIM"
