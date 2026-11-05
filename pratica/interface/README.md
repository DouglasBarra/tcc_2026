# TCC Lab: API e interface web (marco M5)

Instrumento de **demonstração** do framework (Diagnóstico, Análise, Prescrição, Validação) sobre consultas e resultados **reais** gravados em SQLite pelo núcleo (`../nucleo`, pacote `tcclab`) e pelo coletor (`../experimento/coletar.py`). O artigo é sobre o conhecimento gerado, não sobre este sistema: isto é apêndice/apresentação. Documentação completa, com o que foi testado e o que não foi: [`../../knowledge/25-interface.md`](../../knowledge/25-interface.md).

## Como rodar

```bash
cd /Users/douglasbarra/code/TCC/pratica/interface
# uma vez: ambiente Python (3.14) e dependências
/opt/homebrew/bin/python3.14 -m venv .venv
.venv/bin/pip install -e ../nucleo
.venv/bin/pip install -r requirements.lock          # versões fixadas (requirements.txt = só as diretas)
(cd frontend && npm ci)                              # package-lock.json fixa as versões

./run.sh                  # API em 127.0.0.1:8000 + Vite em 127.0.0.1:5173  -> abra http://127.0.0.1:5173
./run.sh --producao       # compila o frontend e serve tudo pela API em http://127.0.0.1:8000
```

Variáveis opcionais: `TCCLAB_RESULTADOS` (padrão `../experimento/resultados`), `TCCLAB_CATALOGO`, `TCCLAB_CONTROLES`, `TCCLAB_PLANOS`, `TCCLAB_PLANOS_CONTROLES`.

**Segurança com a coleta em andamento.** Sem `TCCLAB_AO_VIVO=1` a interface **nunca abre conexão com o SQL Server**: só lê SQLite em modo somente leitura (`mode=ro`). É seguro rodá-la enquanto `coletar.py` usa o banco com exclusividade. Com `TCCLAB_AO_VIVO=1` ligam-se o diagnóstico de consulta colada e a validação antes×depois; ambos recusam com HTTP 409 se a trava global estiver ocupada ou se houver coleta em andamento (arquivo de uma rodada alterado há menos de 3 minutos com experimento `executando`).

## Testes

```bash
.venv/bin/python -m pytest backend -q        # 73 testes: API com SQLite sintético e Conexao FALSA (nunca SQL Server)
cd frontend && npm test && npm run build     # 118 testes (vitest) e build de produção
```

Os dados sintéticos existem **só** em `backend/tests/` e `frontend/src/__testes__/`, rotulados como tal; a interface em uso real lê apenas SQLite gravado por `tcclab`.

## Estrutura

```
backend/tcclab_api/   config, leitura (SQLite ro), catalogo_svc, adaptadores (tcclab.analise / tcclab.diagnostico),
                      planos_svc, servicos (Validação, runs, escrita), trava, ao_vivo, main (rotas /api)
backend/tests/        pytest, fake_conexao.py (Conexao falsa), fixtures de plano XML (capturas reais do M0)
frontend/src/         telas/ (Catálogo, Consulta com abas ①-④, Histórico, Ambiente), componentes/, lib/ (formatação pt-BR, origem, rotas)
dados-dev/            cópia de uma rodada piloto para desenvolver (ignorada; ver .gitignore)
ao-vivo/              SQLite das validações ao vivo (criada sob demanda; nunca dentro de uma rodada de coleta)
```

## API (prefixo `/api`)

| Rota | Função |
|---|---|
| `GET /estado` | versão, catálogo, módulos disponíveis (`analise`, `diagnostico`), estado do modo ao vivo, trava, coletas em andamento |
| `GET /rodadas`, `GET /rodadas/{r}` | rodadas (coleta, piloto, abortada, ao vivo), estado, experimentos, registro da coleta |
| `GET /rodadas/{r}/ambiente` | tabela `environment` (+ parâmetros da rodada) |
| `GET /rodadas/{r}/runs` | histórico de runs (filtros consulta/variante/regime) com medianas |
| `GET /rodadas/{r}/analise[?calcular=true]` | análise entre consultas (`tcclab.analise`); só calcula quando pedido |
| `GET /catalogo`, `GET /consultas/{id}` | consultas, variantes planejadas, DDL de reversão |
| `GET /rodadas/{r}/consultas/{id}/validacao` | grupos por variante×regime×métrica, comparações, veredictos, determinismo, runs, registro da coleta |
| `GET .../repeticoes?metrica=`, `GET .../escrita` | pontos para o gráfico; custo de escrita por operação |
| `GET /rodadas/{r}/planos/{plan_id}`, `GET /consultas/{id}/planos-salvos[/{rotulo}]` | XML do plano e resumo de operadores (`tcclab.plano`) |
| `POST /consultas/{id}/diagnostico` | `tcclab.diagnostico` sobre o SQL do catálogo e um plano salvo (offline) |
| `POST /ao-vivo/diagnosticar`, `POST /ao-vivo/validar`, `GET /ao-vivo/trabalhos[/{id}]` | caminhos ao vivo (desligados por padrão, atrás da trava) |

Docs interativas do FastAPI: `/api/docs`.
