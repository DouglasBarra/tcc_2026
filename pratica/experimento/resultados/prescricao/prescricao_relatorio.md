# Avaliação offline da Prescrição (camadas ② e ③)

> Gerado em 26/10/2026 por `experimento/avaliar_prescricao.py`. Offline: nenhuma conexão ao SQL Server. Planos: `experimento/planos/<ID>/original.{estimado,real}.sqlplan` (linha de base, sem índices não clusterizados, compat. 160, MAXDOP 1). Esquema: `experimento/schema_so2010.json` (só PKs clusterizadas; sem densidades de estatística).

Proveniência (SHA-256, 12 primeiros caracteres): `analise_indices.py` b9320b2ed25d; `prescricao.py` 372e93bd6835; `schema.py` 91beb38ef430; `catalogo.toml` 1f4b960edf2a; `schema_so2010.json` 118d543dc0c2; sqlglot 30.21.0.

## LIMITAÇÃO (leia antes de citar qualquer número)

As regras da análise e da prescrição foram **derivadas dos mesmos casos do catálogo** que aparecem aqui (e, em parte, calibradas com as medições da R1 desses casos: o custo de 4 páginas por Key Lookup e o modelo de tamanho do índice). A concordância abaixo é, portanto, **teste de regressão e de coerência entre o catálogo (escrito à mão pelo autor) e o gerador**, e **não prova de generalização**. O conjunto `heldout.toml` é reservado à validação do Diagnóstico e não foi usado. Divergências estão listadas na seção 4, sem filtro. Para as consultas AP3 o cenário principal **informa ao gerador as colunas da reescrita do catálogo** (as colunas necessárias só o autor conhece): a concordância de INCLUDE e de RW-03 nesses casos é parcialmente circular.

**Nenhuma reescrita é dada como verificada neste relatório.** A verificação de resultado contra o banco **foi executada** (seção 6).

## 1. Resumo por cenário

Cenários: `real` = plano real; `estimado` = plano estimado; `so_sql` = sem plano; `real_regra_literal` = plano real e só o limiar de seletividade (20%); `real_sem_colunas` = AP3 sem informar as colunas (índice comparado ao `indice_original` do catálogo, sem reescrita).

| cenário | índice: igual | equivalente | diferente | sem prescrição | reescrita: igual | equivalente | diferente | sem prescrição |
|---|---|---|---|---|---|---|---|---|
| real | 19 | 0 | 0 | 0 | 13 | 1 | 0 | 5 |
| estimado | 19 | 0 | 0 | 0 | 13 | 1 | 0 | 5 |
| so_sql | 19 | 0 | 0 | 0 | 13 | 1 | 0 | 5 |
| real_regra_literal | 16 | 0 | 3 | 0 | 13 | 1 | 0 | 5 |
| real_sem_colunas | 3 | 0 | 1 | 0 | 0 | 0 | 0 | 4 |

Totais por cenário contam consultas do catálogo (19; `real_sem_colunas` só as 4 de AP3). "Sem prescrição" na reescrita inclui as 5 consultas AP4, que não têm reescrita no catálogo nem na ferramenta.

## 2. Consulta a consulta (cenário `real`)

| consulta | AP | papel | índice | reescrita | causas (camada ②) | observação |
|---|---|---|---|---|---|---|
| F1 | AP1 | principal | igual | igual (RW-01) | Posts:predicado_nao_sargavel/indice_ausente |  |
| F2 | AP1 | principal | igual | equivalente (RW-01) | Users:predicado_nao_sargavel/indice_ausente | reescrita: difere só por caixa de literal de texto ou ordem dos lados de uma igualdade (colação insensível) |
| F3 | AP1 | principal | igual | igual (RW-01) | Votes:predicado_nao_sargavel/indice_ausente |  |
| F4 | AP1 | principal | igual | igual (RW-01) | Posts:predicado_nao_sargavel/indice_ausente |  |
| F5 | AP1 | controle | igual | igual (RW-01) | Votes:predicado_nao_sargavel/indice_ausente |  |
| S1 | AP2 | principal | igual | igual (RW-02) | Posts:subconsulta_correlacionada_repetida/indice_ausente; Users:indice_ausente | adicional: CREATE NONCLUSTERED INDEX IX_Users_Reputation_cob ON dbo.Users (Reputation) INCLUDE (DisplayName) |
| S2 | AP2 | principal | igual | igual (RW-02) | Votes:subconsulta_correlacionada_repetida/indice_ausente; Posts:indice_ausente | adicional: CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId ON dbo.Posts (OwnerUserId) |
| S3 | AP2 | principal | igual | igual (RW-02) | Comments:subconsulta_correlacionada_repetida/indice_ausente; Posts:indice_ausente | adicional: CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId ON dbo.Posts (OwnerUserId) |
| S4 | AP2 | principal | igual | igual (RW-02) | Posts:indice_ausente; Users:indice_ausente | adicional: CREATE NONCLUSTERED INDEX IX_Users_Reputation_cob ON dbo.Users (Reputation) INCLUDE (DisplayName) |
| S5 | AP2 | controle | igual | igual (RW-02) | Badges:indice_ausente; Users:indice_ausente | adicional: CREATE NONCLUSTERED INDEX IX_Users_Reputation_cob ON dbo.Users (Reputation) INCLUDE (DisplayName) |
| C1 | AP3 | principal | igual | igual (RW-03) | Posts:indice_ausente |  |
| C2 | AP3 | principal | igual | igual (RW-03) | Posts:indice_ausente |  |
| C3 | AP3 | principal | igual | igual (RW-03) | Comments:indice_ausente |  |
| C4 | AP3 | principal | igual | igual (RW-03) | Users:indice_ausente |  |
| V1 | AP4 | principal | igual | sem prescrição (-) | Votes:indice_ausente | reescrita: nem a ferramenta nem o catálogo têm reescrita |
| V2 | AP4 | principal | igual | sem prescrição (-) | Comments:indice_ausente | reescrita: nem a ferramenta nem o catálogo têm reescrita |
| V3 | AP4 | principal | igual | sem prescrição (-) | Badges:indice_ausente | reescrita: nem a ferramenta nem o catálogo têm reescrita |
| V4 | AP4 | principal | igual | sem prescrição (-) | Posts:indice_ausente | reescrita: nem a ferramenta nem o catálogo têm reescrita |
| V5 | AP4 | principal | igual | sem prescrição (-) | Users:indice_ausente | reescrita: nem a ferramenta nem o catálogo têm reescrita |

## 3. Diferenças entre cenários

Consultas cuja classe de índice muda conforme o cenário (cada linha mostra o porquê):

- **F4**: `real` = igual; `estimado` = igual; `so_sql` = igual; `real_regra_literal` = diferente (a ferramenta recomenda NÃO indexar Posts: Não indexar: regra literal: filtro devolve 58,1% da tabela (limiar 20,0%).)
- **S1**: `real` = igual; `estimado` = igual; `so_sql` = igual; `real_regra_literal` = diferente (a ferramenta recomenda NÃO indexar Posts: Não indexar: regra literal: filtro devolve 33,1% da tabela (limiar 20,0%).)
- **S4**: `real` = igual; `estimado` = igual; `so_sql` = igual; `real_regra_literal` = diferente (a ferramenta recomenda NÃO indexar Posts: Não indexar: regra literal: filtro devolve 33,1% da tabela (limiar 20,0%).)

## 4. Divergências (todos os cenários), sem filtro

- **F4** (AP1), cenário `real_regra_literal`: índice diferente — a ferramenta recomenda NÃO indexar Posts: Não indexar: regra literal: filtro devolve 58,1% da tabela (limiar 20,0%).; reescrita igual
- **S1** (AP2), cenário `real_regra_literal`: índice diferente — a ferramenta recomenda NÃO indexar Posts: Não indexar: regra literal: filtro devolve 33,1% da tabela (limiar 20,0%).; reescrita igual
- **S4** (AP2), cenário `real_regra_literal`: índice diferente — a ferramenta recomenda NÃO indexar Posts: Não indexar: regra literal: filtro devolve 33,1% da tabela (limiar 20,0%).; reescrita igual
- **C4** (AP3), cenário `real_sem_colunas`: índice diferente — a ferramenta recomenda NÃO indexar Users: Não indexar: índice não cobrindo: ~14.627 páginas em Key Lookups (3.656 linhas x 4) contra 7.405 da varredura; filtro devolve 1,22% da tabela (limiar 20,0%). Reduza a projeção (RW-03) e reavalie: um índice que cobre a consulta custa menos que a varredura mesmo com seletividade baixa.; reescrita sem prescrição — nem a ferramenta nem o catálogo têm reescrita

Índices que a ferramenta propõe **além** do índice do catálogo (a classe acima só considera o índice da tabela do catálogo):

- S1: CREATE NONCLUSTERED INDEX IX_Users_Reputation_cob ON dbo.Users (Reputation) INCLUDE (DisplayName)
- S2: CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId ON dbo.Posts (OwnerUserId)
- S3: CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId ON dbo.Posts (OwnerUserId)
- S4: CREATE NONCLUSTERED INDEX IX_Users_Reputation_cob ON dbo.Users (Reputation) INCLUDE (DisplayName)
- S5: CREATE NONCLUSTERED INDEX IX_Users_Reputation_cob ON dbo.Users (Reputation) INCLUDE (DisplayName)

## 6. Verificação de equivalência de resultado (contra o banco)

| consulta | regra | status | linhas | detalhe |
|---|---|---|---|---|
| F1 | RW-01 | equivalente_nos_dados | 1 |  |
| F2 | RW-01 | equivalente_nos_dados | 1 |  |
| F3 | RW-01 | equivalente_nos_dados | 9 |  |
| F4 | RW-01 | equivalente_nos_dados | 1 |  |
| F5 | RW-01 | equivalente_nos_dados | 9 |  |
| S1 | RW-02 | equivalente_nos_dados | 4020 |  |
| S2 | RW-02 | equivalente_nos_dados | 11354 |  |
| S3 | RW-02 | equivalente_nos_dados | 11354 |  |
| S4 | RW-02 | equivalente_nos_dados | 2706 |  |
| S5 | RW-02 | equivalente_nos_dados | 4020 |  |
| C1 | RW-03 | equivalente_nos_dados | 11354 |  |
| C2 | RW-03 | equivalente_nos_dados | 53 |  |
| C3 | RW-03 | equivalente_nos_dados | 13360 |  |
| C4 | RW-03 | equivalente_nos_dados | 3656 |  |

`equivalente_nos_dados` = mesmo *hash* de multiconjunto de linhas no banco consultado, uma execução; não prova equivalência lógica.

## 7. Reprodução

```bash
cd /Users/douglasbarra/code/TCC/pratica/nucleo
.venv/bin/python ../experimento/avaliar_prescricao.py --medidos <cópia de resultados/r1-compat160/tcclab.sqlite>
```
