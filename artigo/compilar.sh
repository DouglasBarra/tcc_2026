#!/usr/bin/env bash
# Compila artigo/main.tex em PDF (build/main.pdf) com o Tectonic.
#
# Uso:
#   ./compilar.sh            compila (pode ser repetido; só reescreve build/)
#   ./compilar.sh limpar     apaga build/ e compila do zero
#
# Requisitos: Tectonic (brew install tectonic). Na primeira execução ele baixa
# os pacotes do LaTeX (abntex2cite, caption, etc.) e os guarda em cache em
# ~/Library/Caches/TectonicProject.Tectonic; depois disso compila sem rede.
# O Tectonic roda o motor XeTeX, o BibTeX (estilo abntex2-unesc.bst) e as
# repetições necessárias sozinho. No Overleaf o motor é o pdfLaTeX; ver
# knowledge/21-compilacao-do-artigo.md para a comparação entre os dois.
#
# Arquivos de suporte (copiados do projeto Overleaf): UNESC.cls e abntex2-unesc.bst.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v tectonic >/dev/null 2>&1; then
  echo "Tectonic não encontrado. Instale com: brew install tectonic" >&2
  exit 1
fi

if [[ "${1:-}" == "limpar" ]]; then
  rm -rf build
fi
mkdir -p build

# --keep-logs e --keep-intermediates deixam main.log, main.blg, main.aux e main.bbl em build/.
# A saída do Tectonic é guardada em build/tectonic.log (sem as linhas de download).
set +e
tectonic -X compile --keep-logs --keep-intermediates --outdir build main.tex >build/tectonic.log 2>&1
status=$?
set -e
grep -v '^note: downloading' build/tectonic.log >build/tectonic.log.tmp || true
mv build/tectonic.log.tmp build/tectonic.log

if [[ $status -ne 0 ]]; then
  echo "FALHOU (código $status). Últimas linhas de build/tectonic.log:" >&2
  tail -30 build/tectonic.log >&2
  exit $status
fi

echo "PDF: $(pwd)/build/main.pdf"

# Resumo rápido da compilação.
if command -v pdfinfo >/dev/null 2>&1; then
  echo "Páginas: $(pdfinfo build/main.pdf | awk '/^Pages:/ {print $2}')"
fi
echo "Overfull hbox: $(grep -c 'Overfull \\hbox' build/main.log || true)"
echo "Underfull hbox: $(grep -c 'Underfull \\hbox' build/main.log || true)"
echo "Citações/referências indefinidas: $(grep -ci 'undefined' build/main.log || true)"
# O estilo abntex2-unesc.bst tem funções comentadas; o BibTeX registra esses erros
# também no Overleaf. A lista sai pronta (main.bbl); ver o relatório 21.
echo "Mensagens do BibTeX (build/main.blg): $(grep -E 'error message|warning message' build/main.blg || echo 'nenhuma')"
