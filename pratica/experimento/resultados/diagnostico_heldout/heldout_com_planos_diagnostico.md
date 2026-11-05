# Validação do Diagnóstico em conjunto separado (heldout)

Execução `com_planos`, gerada em 2026-10-25 por `experimento/avaliar_diagnostico.py`. sqlglot 30.21.0. 13 consultas. Offline (nenhuma consulta ao SQL Server).

## Integridade (congelamento do pré-registro)

- Conjunto `heldout.toml`: SHA-256 `c3aca1c37476cc643eab4194a3d830fe33c58061d6b402dd309ff984cbdaa07d`; coincide com o pré-registro: sim.
- Detector `diagnostico.py`: SHA-256 `c12466250ea93d9a346e331411aeb87a9e7a06c0b292c2995c4e602ad4a06c90`; coincide com o pré-registro: sim.
- Limiares: valores padrão do detector congelado.

## Estado dos modos

- só SQL: executado (13 casos).
- SQL + plano estimado: executado (13 casos).
- SQL + plano real: executado (13 casos).

## Resumo por modo

| Modo | Com esperado: acertos (estrito) | Omissões | Limpas sem falso positivo | Consultas com falso positivo | Falsos positivos (classes) | Proibidos violados |
|---|---|---|---|---|---|---|
| só SQL | 5/10 (50%; IC95% de Wilson 24% a 76%) | 5 | 3/3 (100%; IC95% de Wilson 44% a 100%) | 0/13 (0%; IC95% de Wilson 0% a 23%) | 0 | 0 em 0 consulta(s) |
| SQL + plano estimado | 10/10 (100%; IC95% de Wilson 72% a 100%) | 0 | 3/3 (100%; IC95% de Wilson 44% a 100%) | 0/13 (0%; IC95% de Wilson 0% a 23%) | 0 | 0 em 0 consulta(s) |
| SQL + plano real | 10/10 (100%; IC95% de Wilson 72% a 100%) | 0 | 3/3 (100%; IC95% de Wilson 44% a 100%) | 0/13 (0%; IC95% de Wilson 0% a 23%) | 0 | 0 em 0 consulta(s) |

**Leitura dentro do alcance do modo** (NÃO é a regra de pontuação do pré-registro; só separa a omissão por limite do modo da omissão por defeito do detector):

| Modo | Acertos considerando só as classes ao alcance | Alcance |
|---|---|---|
| só SQL | 10/10 (100%; IC95% de Wilson 72% a 100%) | AP1 a AP3 (AP4 exige plano) |
| SQL + plano estimado | 10/10 (100%; IC95% de Wilson 72% a 100%) | AP1 a AP4 |
| SQL + plano real | 10/10 (100%; IC95% de Wilson 72% a 100%) | AP1 a AP4 |

## Por consulta: só SQL

| Consulta | Título | Esperado | Tolerado | Apontadas | Resultado (estrito) | Omissões | Falsos positivos | Proibidos violados | Resultado no alcance |
|---|---|---|---|---|---|---|---|---|---|
| H1 | LEFT sobre DisplayName | AP1 | AP4 | AP1 | acerto | - | - | - | acerto |
| H2 | YEAR sobre Comments.CreationDate | AP1 | AP4 | AP1 | acerto | - | - | - | acerto |
| H3 | ISNULL sobre coluna anulavel no filtro | AP1 | AP4 | AP1 | acerto | - | - | - | acerto |
| H4 | CAST da coluna para texto no filtro | AP1 | AP4 | AP1 | acerto | - | - | - | acerto |
| H5 | subconsulta escalar correlacionada com TOP 1 e ORDER BY | AP2 | AP4 | AP2 | acerto | - | - | - | acerto |
| H6 | EXISTS correlacionado (semijuncao, NAO e subconsulta escalar | AP4 | - | - | omissao | AP4 | - | - | acerto |
| H7 | SELECT * em Badges por UserId | AP3 AP4 | - | AP3 | omissao | AP4 | - | - | acerto |
| H8 | SELECT * em PostLinks por PostId | AP3 AP4 | - | AP3 | omissao | AP4 | - | - | acerto |
| H9 | filtro de igualdade em Posts.ParentId sem indice | AP4 | - | - | omissao | AP4 | - | - | acerto |
| H10 | filtro composto de igualdade em Votes sem indice | AP4 | - | - | omissao | AP4 | - | - | acerto |
| H11 | junção entre chaves primarias com intervalo | - | - | - | limpa_correta | - | - | - | limpa_correta |
| H12 | contagem total de Users (varredura legitima sem filtro) | - | - | - | limpa_correta | - | - | - | limpa_correta |
| H13 | busca de tres chaves primarias com colunas listadas | - | - | - | limpa_correta | - | - | - | limpa_correta |

**Omissões e falsos positivos (só SQL), como saíram:**
- H6: **omissão** de AP4 (inatingível sem plano: nenhuma regra de SQL mapeia para AP4). Apontadas: -.
- H7: **omissão** de AP4 (inatingível sem plano: nenhuma regra de SQL mapeia para AP4). Apontadas: AP3.
- H8: **omissão** de AP4 (inatingível sem plano: nenhuma regra de SQL mapeia para AP4). Apontadas: AP3.
- H9: **omissão** de AP4 (inatingível sem plano: nenhuma regra de SQL mapeia para AP4). Apontadas: -.
- H10: **omissão** de AP4 (inatingível sem plano: nenhuma regra de SQL mapeia para AP4). Apontadas: -.

## Por consulta: SQL + plano estimado

| Consulta | Título | Esperado | Tolerado | Apontadas | Resultado (estrito) | Omissões | Falsos positivos | Proibidos violados | Resultado no alcance |
|---|---|---|---|---|---|---|---|---|---|
| H1 | LEFT sobre DisplayName | AP1 | AP4 | AP1 AP4 | acerto | - | - | - | acerto |
| H2 | YEAR sobre Comments.CreationDate | AP1 | AP4 | AP1 | acerto | - | - | - | acerto |
| H3 | ISNULL sobre coluna anulavel no filtro | AP1 | AP4 | AP1 AP4 | acerto | - | - | - | acerto |
| H4 | CAST da coluna para texto no filtro | AP1 | AP4 | AP1 AP4 | acerto | - | - | - | acerto |
| H5 | subconsulta escalar correlacionada com TOP 1 e ORDER BY | AP2 | AP4 | AP2 AP4 | acerto | - | - | - | acerto |
| H6 | EXISTS correlacionado (semijuncao, NAO e subconsulta escalar | AP4 | - | AP4 | acerto | - | - | - | acerto |
| H7 | SELECT * em Badges por UserId | AP3 AP4 | - | AP3 AP4 | acerto | - | - | - | acerto |
| H8 | SELECT * em PostLinks por PostId | AP3 AP4 | - | AP3 AP4 | acerto | - | - | - | acerto |
| H9 | filtro de igualdade em Posts.ParentId sem indice | AP4 | - | AP4 | acerto | - | - | - | acerto |
| H10 | filtro composto de igualdade em Votes sem indice | AP4 | - | AP4 | acerto | - | - | - | acerto |
| H11 | junção entre chaves primarias com intervalo | - | - | - | limpa_correta | - | - | - | limpa_correta |
| H12 | contagem total de Users (varredura legitima sem filtro) | - | - | - | limpa_correta | - | - | - | limpa_correta |
| H13 | busca de tres chaves primarias com colunas listadas | - | - | - | limpa_correta | - | - | - | limpa_correta |

## Por consulta: SQL + plano real

| Consulta | Título | Esperado | Tolerado | Apontadas | Resultado (estrito) | Omissões | Falsos positivos | Proibidos violados | Resultado no alcance |
|---|---|---|---|---|---|---|---|---|---|
| H1 | LEFT sobre DisplayName | AP1 | AP4 | AP1 AP4 | acerto | - | - | - | acerto |
| H2 | YEAR sobre Comments.CreationDate | AP1 | AP4 | AP1 | acerto | - | - | - | acerto |
| H3 | ISNULL sobre coluna anulavel no filtro | AP1 | AP4 | AP1 AP4 | acerto | - | - | - | acerto |
| H4 | CAST da coluna para texto no filtro | AP1 | AP4 | AP1 AP4 | acerto | - | - | - | acerto |
| H5 | subconsulta escalar correlacionada com TOP 1 e ORDER BY | AP2 | AP4 | AP2 AP4 | acerto | - | - | - | acerto |
| H6 | EXISTS correlacionado (semijuncao, NAO e subconsulta escalar | AP4 | - | AP4 | acerto | - | - | - | acerto |
| H7 | SELECT * em Badges por UserId | AP3 AP4 | - | AP3 AP4 | acerto | - | - | - | acerto |
| H8 | SELECT * em PostLinks por PostId | AP3 AP4 | - | AP3 AP4 | acerto | - | - | - | acerto |
| H9 | filtro de igualdade em Posts.ParentId sem indice | AP4 | - | AP4 | acerto | - | - | - | acerto |
| H10 | filtro composto de igualdade em Votes sem indice | AP4 | - | AP4 | acerto | - | - | - | acerto |
| H11 | junção entre chaves primarias com intervalo | - | - | - | limpa_correta | - | - | - | limpa_correta |
| H12 | contagem total de Users (varredura legitima sem filtro) | - | - | - | limpa_correta | - | - | - | limpa_correta |
| H13 | busca de tres chaves primarias com colunas listadas | - | - | - | limpa_correta | - | - | - | limpa_correta |

## Por regra

VP/FN: a classe da regra é `esperada` na consulta. FP/VN: a classe não é esperada nem tolerada (FP proibido = violou `proibido`). "Toleradas": a regra disparou numa classe tolerada (não pontuada). Regras de plano só aparecem nos modos com plano.


**só SQL**

| Regra | AP | VP | FN | FP (proibido) | VN | Toleradas | Sensib. | Precisão |
|---|---|---|---|---|---|---|---|---|
| S1 | AP3 | 2 | 0 | 0 (0) | 11 | 0 | 1,0 | 1,0 |
| S2 | AP1 | 4 | 0 | 0 (0) | 9 | 0 | 1,0 | 1,0 |
| S3 | AP2 | 1 | 0 | 0 (0) | 12 | 0 | 1,0 | 1,0 |

**SQL + plano estimado**

| Regra | AP | VP | FN | FP (proibido) | VN | Toleradas | Sensib. | Precisão |
|---|---|---|---|---|---|---|---|---|
| S1 | AP3 | 2 | 0 | 0 (0) | 11 | 0 | 1,0 | 1,0 |
| S2 | AP1 | 4 | 0 | 0 (0) | 9 | 0 | 1,0 | 1,0 |
| S3 | AP2 | 1 | 0 | 0 (0) | 12 | 0 | 1,0 | 1,0 |
| P1 | AP4 | 5 | 0 | 0 (0) | 3 | 4 | 1,0 | 1,0 |
| P2 | AP3 | 0 | 2 | 0 (0) | 11 | 0 | 0,0 |  |
| P3 | AP1 | 4 | 0 | 0 (0) | 9 | 0 | 1,0 | 1,0 |
| P4 | AP1 | 0 | 0 | 0 (0) | 9 | 0 |  |  |
| P5 | AP4 | 4 | 1 | 0 (0) | 3 | 1 | 0,8 | 1,0 |
| P6 | AP2 | 0 | 1 | 0 (0) | 12 | 0 | 0,0 |  |

**SQL + plano real**

| Regra | AP | VP | FN | FP (proibido) | VN | Toleradas | Sensib. | Precisão |
|---|---|---|---|---|---|---|---|---|
| S1 | AP3 | 2 | 0 | 0 (0) | 11 | 0 | 1,0 | 1,0 |
| S2 | AP1 | 4 | 0 | 0 (0) | 9 | 0 | 1,0 | 1,0 |
| S3 | AP2 | 1 | 0 | 0 (0) | 12 | 0 | 1,0 | 1,0 |
| P1 | AP4 | 5 | 0 | 0 (0) | 3 | 4 | 1,0 | 1,0 |
| P2 | AP3 | 0 | 2 | 0 (0) | 11 | 0 | 0,0 |  |
| P3 | AP1 | 4 | 0 | 0 (0) | 9 | 0 | 1,0 | 1,0 |
| P4 | AP1 | 0 | 0 | 0 (0) | 9 | 0 |  |  |
| P5 | AP4 | 4 | 1 | 0 (0) | 3 | 1 | 0,8 | 1,0 |
| P6 | AP2 | 0 | 1 | 0 (0) | 12 | 0 | 0,0 |  |

## Ressalvas

- **n pequeno e exploratório.** São 13 consultas (10 com classe esperada e 3 limpas); os intervalos de Wilson (95%) acima são largos e nenhum resultado aqui estabelece taxa de acerto ou de falso positivo em código real. Mesmo 10/10 deixaria o limite inferior do IC95% de Wilson em ~72%.
- **Rótulos feitos pela coordenação do projeto (assistente), antes da execução**, mas não por pessoa independente do detector; o autor do TCC ainda não os revisou.
- **Pontuação estrita em modo só SQL:** consultas com AP4 em `esperado` (H6, H7, H8, H9, H10) contam como omissão porque nenhuma regra de SQL pode apontar AP4 (a classe vem do plano). Isso é limite do modo, não necessariamente defeito do detector; por isso a leitura "dentro do alcance" vem ao lado, sem substituir a estrita.
- **Execução com plano (estimado e real): pendente** até depois do fim da R1. Rodar `avaliar_diagnostico.py --catalogo heldout.toml` (sem `--so-sql`) depois que `salvar_planos.py --catalogo heldout.toml --saida planos_heldout` gerar os planos; essa segunda execução usa uma `--execucao` com outro nome.
- Se o detector ou os limiares forem alterados depois desta execução, o conjunto passa a ser de desenvolvimento (PRE-REGISTRO.md, seção 5, regra iii).
