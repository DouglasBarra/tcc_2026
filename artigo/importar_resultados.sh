#!/usr/bin/env bash
# Copia tabelas, figuras e numeros gerados por `tcclab relatorio` para artigo/dados/<nome>/.
# Nada de numero digitado a mao: o texto usa \input das tabelas e consulta numeros-do-artigo.md.
# Uso: ./importar_resultados.sh <pasta-de-saida-da-analise> <nome>      ex.: ./importar_resultados.sh ../pratica/experimento/analise/r1-compat160 r1
set -euo pipefail
ORIGEM="${1:?pasta de saida do tcclab relatorio}"; NOME="${2:?nome curto (ex.: r1)}"
DEST="$(cd "$(dirname "$0")" && pwd)/dados/${NOME}"
mkdir -p "${DEST}/tabelas" "${DEST}/figuras" "${DEST}/csv"
cp "${ORIGEM}"/tabelas/*.tex "${DEST}/tabelas/"
cp "${ORIGEM}"/figuras/*.pdf "${DEST}/figuras/"
cp "${ORIGEM}"/comparisons.csv "${ORIGEM}"/aggregate.csv "${ORIGEM}"/escrita.csv "${ORIGEM}"/determinism.csv "${ORIGEM}"/summary.csv "${ORIGEM}"/queries.csv "${DEST}/csv/"
cp "${ORIGEM}"/numeros-do-artigo.md "${ORIGEM}"/numeros-do-artigo.json "${ORIGEM}"/criterios.json "${DEST}/"
echo "copiado para ${DEST}"; ls "${DEST}/tabelas" "${DEST}/figuras"
