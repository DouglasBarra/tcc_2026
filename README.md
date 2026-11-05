# Indexação e otimização de consultas em SQL Server: código e dados do TCC

Código, consultas e dados brutos do experimento do TCC "Boas práticas de indexação e otimização de consultas em SQL Server: uma abordagem metodológica baseada em métricas de performance" (Ciência da Computação, Unesc, 2026). Autor: Douglas Gonçalves E Barra. Orientadora: Ana Cláudia Garcia Barbosa, Ma.

O experimento aplica um *framework* de quatro camadas (diagnóstico, análise, prescrição e validação) a 19 consultas com antipadrões sobre o banco Stack Overflow 2010. Ele compara leituras lógicas, tempo de processamento e tempo de execução antes e depois das recomendações, e também o custo de escrita dos índices criados.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `pratica/infra/` | `docker-compose.yml` do SQL Server 2022 Developer e scripts `up.sh`, `down.sh` e `sqlcmd.sh` |
| `pratica/dados/` | `extrair.py` (descompacta o conjunto de dados) e os scripts SQL de preparação (`sql/01` a `sql/10`) |
| `pratica/nucleo/` | pacote Python `tcclab`, que mede em três canais (STATISTICS IO/TIME, plano real e DMV/Query Store) e grava em SQLite, com testes |
| `pratica/experimento/` | catálogo das 19 consultas (`catalogo.toml`), pré-registro (`PRE-REGISTRO.md`), coletor (`coletar.py`), custo de escrita, iteração 2 e os resultados de cada rodada (`resultados/*/tcclab.sqlite`) |
| `pratica/interface/` | interface web de demonstração (API em Python e *frontend* em Vite), que só lê os SQLite |
| `artigo/` | fontes LaTeX do artigo, os dados usados nas tabelas (`dados/r2/`) e os scripts que geram as tabelas a partir dos SQLite |

## Requisitos

- Docker (Docker Desktop no macOS) com pelo menos 6 GB de memória para a VM
- Python 3.12 ou mais novo (o experimento usou 3.14)
- Node.js, só para a interface
- Cerca de 20 GB livres em disco

## Como executar

**1. Subir o SQL Server**

```bash
cd pratica/infra
cp .env.example .env        # troque a senha MSSQL_SA_PASSWORD
./scripts/up.sh
```

**2. Obter e restaurar o conjunto de dados.** O Stack Overflow 2010 (1,17 GB compactado) não está no repositório. Baixe `StackOverflow2010.7z` da distribuição de Brent Ozar (https://www.brentozar.com/archive/2015/10/how-to-download-the-stack-overflow-database-via-bittorrent/) para `pratica/dados/`. Depois:

```bash
cd pratica/dados
python3 -m venv .venv && .venv/bin/pip install py7zr && .venv/bin/python extrair.py
```

Anexe os arquivos extraídos ao SQL Server como `StackOverflow2010` e rode os scripts `pratica/dados/sql/01` a `10`, na ordem, com `pratica/infra/scripts/sqlcmd.sh`. O script `10-clone-experimento.sql` gera a cópia de segurança de onde cada consulta restaura o clone `so_exp`.

**3. Instalar o núcleo de medição e rodar os testes**

```bash
cd pratica/nucleo
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/python -m tcclab ping
```

**4. Rodar o experimento**

```bash
cd pratica/experimento
./rodar_rodadas.sh r2       # compatibilidade 160 (principal) e 100 (sensibilidade); leva de 1 a 2 horas
./pos_r2.sh                 # custo de escrita, iteração 2 (pós-hoc) e verificação das reescritas
```

Cada rodada grava em `pratica/experimento/resultados/<rodada>/tcclab.sqlite`.

**5. Gerar as tabelas e figuras do artigo**

```bash
cd pratica/nucleo && .venv/bin/pip install -e ".[analise]" && .venv/bin/python -m tcclab relatorio --help
cd ../../artigo
./importar_resultados.sh <pasta do relatório> r2
./gerar_tabelas_extras.py r2 ../pratica/experimento/resultados/escrita-corrigida/tcclab.sqlite
```

**Interface (opcional):** veja `pratica/interface/README.md`.

## Observações

- Os resultados do artigo vêm da rodada R2 (`resultados/r2-compat160`) e da rodada dedicada de escrita (`resultados/escrita-corrigida`). Os SQLite de todas as rodadas estão incluídos, e não é preciso executar o experimento para conferir os números.
- O SQL Server rodou como imagem amd64 sob emulação em Apple Silicon. Os tempos absolutos dependem da máquina; as leituras lógicas, não.
- Os comandos que alteram o estado do banco (`DBCC DROPCLEANBUFFERS`, DDL) recusam bancos fora da lista de permissão (`TCCLAB_PERMITIDOS`).
- Alguns arquivos de documentação ainda trazem caminhos absolutos da máquina do autor (`/Users/...`). Troque-os pelos caminhos relativos do repositório.

## Licença dos dados

Os dados do Stack Overflow são licenciados sob CC BY-SA 4.0 (Stack Exchange, Inc.), e o uso exige atribuição aos autores.
