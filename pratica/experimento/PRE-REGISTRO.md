# Pré-registro do experimento principal

**Data do registro:** 25/10/2026, antes de qualquer coleta da rodada principal. Os pilotos (`resultados/piloto-*`) servem só para depurar o instrumento e estimar duração; **não entram na análise**.
**Status das decisões:** tomadas pelo assistente sob autonomia dada pelo autor em 25/10/2026, seguindo as recomendações já apresentadas a ele (relatórios 16, 19 e 20). Todas são **provisórias até o autor confirmar**; as que dependem da orientadora estão marcadas.

## 1. Hipótese e critérios

- **H1 (do projeto, parametrizável):** aplicar as recomendações das camadas de análise e de prescrição reduz em **pelo menos 20%** a mediana das leituras lógicas das consultas com antipadrões. O limiar de 20% é o do projeto e deve ser confirmado com a orientadora (decisão 5 do autor, `knowledge/18`).
- **Tratamento (por consulta):** a **prescrição** do *framework*, isto é, a variante `reescrita+indice` (classes com reescrita) ou `indice` (classe AP4, que não tem reescrita). Linha de base: `original`, sem índices não clusterizados.
- **Melhoria por consulta:** razão das medianas de leituras lógicas (prescrição/original) ≤ 0,80. As leituras lógicas são determinísticas com `MAXDOP 1` e plano fixo; se o desvio padrão for zero, o intervalo de confiança da razão é degenerado e o critério reduz-se à comparação direta.
- **Generalização:** teste dos postos sinalizados de Wilcoxon, pareado entre consultas, sobre o logaritmo da razão, por métrica (leituras lógicas, CPU, tempo de execução), com correção de Holm para as três métricas, **α = 0,05**. Reportar também a redução mediana percentual com IC95% por *bootstrap* percentil entre consultas (10.000 reamostragens, semente fixa). **Teste, α e Holm são proposta do assistente; não há fonte no material da disciplina; confirmar com a orientadora.**
- **Análise principal:** as 17 consultas de papel `principal`. **Análise de sensibilidade:** as 19, com as duas de papel `controle` (F5 e S5), que foram designadas antes da coleta como casos em que o otimizador pode já neutralizar o antipadrão. Os controles são reportados em tabela própria.
- **Custo de escrita:** reportado como razão (com índice / sem índice) por operação (INSERT, UPDATE, DELETE), em leituras lógicas e bytes de log. **Sem limiar de aceitação.** O critério de 10% do relatório 16 (seção 6.4) foi **retirado antes da coleta**: um piloto mostrou que qualquer índice eleva o custo de escrita em dezenas a centenas de porcento (por exemplo, INSERT de 1.000 linhas: +119% de leituras lógicas em `Users` com o índice sobre `DisplayName`), de modo que o limiar rejeitaria todos os índices. O efeito colateral negativo, para fins da regra de iteração do *framework*, é definido apenas como a prescrição **aumentar** as leituras lógicas da própria consulta (razão > 1).

## 2. Ambiente e configuração

- SQL Server 2022 Developer, RTM-CU27 (16.0.4295.3), imagem fixada por *digest* (`pratica/infra/docker-compose.yml`), amd64 emulada em Apple Silicon; 4.096 MB para o SQL Server; 15 CPUs visíveis.
- Banco: **clone `so_exp` restaurado do `.bak` original antes de cada consulta** (estado inicial idêntico; o banco base `StackOverflow2010` nunca é medido). Checksum do `.bak`: `0e7e5cfa…680d992f`.
- **Nível de compatibilidade principal: 160** (padrão do SQL Server 2022, que é a versão sob teste). **Sensibilidade: 100** (nível em que o conjunto de dados é distribuído, com o estimador de cardinalidade legado). Decisão provisória do assistente; **o autor pode inverter**. Ambas as rodadas são reportadas.
- **`MAXDOP` = 1** por configuração do escopo do banco (`ALTER DATABASE SCOPED CONFIGURATION`), igual para todas as variantes. Motivo: com planos paralelos as leituras lógicas variaram até 2% entre execuções idênticas (relatório 20, seção 8.5), e CPU e tempo deixam de ser comparáveis. Limitação: o plano serial pode diferir do plano que o servidor escolheria por padrão.
- Opções de sessão fixas: `NOCOUNT ON`, `LANGUAGE us_english`, `ARITHABORT ON`. *Query Store* ligado em todas as variantes (`QUERY_CAPTURE_MODE = ALL`).
- Memória: `Posts` (6,7 GB) não cabe nos 4 GB do SQL Server; o regime "quente" para essa tabela inclui leituras físicas (no piloto, 800.531 físicas de 800.856 lógicas numa varredura de `Posts`: o regime quente de `Posts` equivale na prática a frio para o SQL Server). **Limitação declarada; a conclusão se ancora nas leituras lógicas.**
- **As leituras lógicas não são idênticas entre quente e frio quando o plano usa *key lookup*:** no piloto, C1 com índice só na chave leu 46.157 páginas no quente e 78.050 no frio (o pré-carregamento assíncrono também conta). Para varreduras e planos sem busca por chave são idênticas. A análise principal das leituras lógicas usa o regime **quente**; o frio é reportado à parte.

## 3. Delineamento

- Catálogo: `catalogo.toml` (19 consultas: 5 AP1, 5 AP2, 4 AP3, 5 AP4), parâmetros literais fixos. Equivalência de resultado (hash de multiconjunto) exigida antes de medir; variante divergente é invalidada e reportada.
- Variantes por consulta: `original`, `reescrita`, `indice`, `reescrita+indice`, `nativo` (índice sugerido pelo plano, quando diferente do índice do *framework*) e braços extras (contraexemplo de índice não cobrindo em S1; ordem invertida das colunas em V4).
- **Ordem ABA:** `original` (A1), demais variantes na ordem do catálogo, `original` (A2). **Regra de deriva:** se a mediana da CPU de A2 diferir da de A1 em mais de **25%** no regime quente, o experimento da consulta é repetido uma vez; a tentativa descartada permanece gravada e marcada.
- Antes de cada bloco: aplicação do DDL da variante, `UPDATE STATISTICS … WITH FULLSCAN` nas tabelas da consulta, limpeza do cache de planos do banco, uma execução com plano real, **2 execuções de aquecimento** descartadas (gravadas como `warmup`).
- **Repetições medidas:** **20** no regime quente e **8** no regime frio (frio = `CHECKPOINT` + `DBCC DROPCLEANBUFFERS` antes de cada repetição; frio apenas para o SQL Server, não para o disco; relatório 19, seção 5). Justificativa: nas medições do M0 a CPU em ms teve CV de 6% a 15% nas varreduras (45% em um bloco com atípicos); com CV de 15%, 20 repetições dão meia-largura de cerca de 5% no IC da mediana, e as leituras lógicas não dependem de N. **Alterado de 30/10/3 para 20/8/2 em 25/10/2026, antes de qualquer coleta, depois que um piloto mostrou que a coleta completa com 30/10 levaria mais de 6 horas por rodada** (a consulta S1 sozinha levou 20 minutos com apenas 3/2 repetições). **Exceção declarada:** o braço de contraexemplo de S1 (reescrita com índice não cobrindo) leva cerca de 94 s por execução e lê cerca de 5 milhões de páginas; usa 3 repetições no quente, 2 no frio e 1 aquecimento. Suas leituras lógicas são determinísticas; o tempo é só descritivo.
- **Valores atípicos:** marcados pelas cercas de Tukey (1,5·IQR) e **nunca descartados**. A análise principal usa a mediana; a sensibilidade repete excluindo os marcados.
- **Rodadas:** R1 (máquina de desenvolvimento em uso por outras tarefas) e R2 (**máquina sem outra carga**, executada depois do término das demais tarefas). As leituras lógicas devem ser **idênticas** nas duas (teste de reprodutibilidade). As métricas de tempo da análise principal vêm de R2; se R2 não for executada, usa-se R1 com a limitação declarada.
- Custo de escrita: 1.000 linhas por operação (`INSERT … SELECT TOP`, `UPDATE TOP` na primeira coluna-chave, `DELETE TOP`) dentro de transação revertida; 10 repetições com e sem o índice da prescrição; tamanho do índice e tempo de criação registrados.

## 4. O que não será feito (para não ser ajustado depois)

- Nenhuma consulta é trocada ou removida depois de ver os resultados; as de equivalência divergente ou erro são reportadas como tal.
- Os limiares (20%, 25% de deriva, α = 0,05) não mudam depois da coleta.
- Resultados sem ganho ou com piora entram na análise e no texto.

## 5. Validação do Diagnóstico (conjunto separado)

**Registrado em 25/10/2026 (cerca de 18h40), antes de qualquer execução do detector sobre o conjunto de validação.**

O módulo `tcclab/diagnostico.py` foi desenvolvido e ajustado sobre o catálogo e os controles limpos (28 consultas), de modo que seus 19/19 acertos e 0/9 falsos positivos nessa amostra medem **regressão, não generalização**. Para estimar o desempenho em consultas não vistas, `experimento/heldout.toml` reúne 13 consultas (4 com função sobre coluna, 1 com subconsulta escalar correlacionada, 2 com `SELECT *`, 2 com varredura por índice ausente, 1 `EXISTS` correlacionado que **não** é antipadrão de subconsulta escalar e 3 limpas), rotuladas por `esperado`, `tolerado` e `proibido` antes da execução. Os limiares do detector são os valores padrão do arquivo congelado abaixo; **todos são suposições** do assistente.

| Arquivo | SHA-256 |
|---|---|
| `experimento/heldout.toml` | `c3aca1c37476cc643eab4194a3d830fe33c58061d6b402dd309ff984cbdaa07d` |
| `nucleo/src/tcclab/diagnostico.py` | `c12466250ea93d9a346e331411aeb87a9e7a06c0b292c2995c4e602ad4a06c90` |
| `experimento/catalogo.toml` (antes da R1) | `1f4b960edf2a2c1f8e27340a35f5a6d8404a90737e79fb120ec35fd554cbec6b` |
| `experimento/controles_limpos.toml` | `37b3a0d5fcb2e7cac6609d2a688274ae70158a4d6f01f53d3b679e081b694ec8` |

Regras: (i) o detector roda **uma vez** sobre o conjunto de validação, em três modos (só SQL, SQL + plano estimado, SQL + plano real); (ii) acertos, omissões, falsos positivos e proibidos violados são reportados como estão; (iii) se o detector for alterado depois, o conjunto passa a ser de desenvolvimento e a execução seguinte é rotulada "após ajuste", com os dois resultados lado a lado; (iv) com n = 13, os intervalos de confiança de Wilson são largos e o resultado é descrito como exploratório. Os planos do conjunto de validação só podem ser gerados depois do fim da rodada R1, porque a geração usa o banco.

## 6. Alteração registrada antes dos resultados da R1 (25/10/2026, 18h40)

A primeira tentativa da R1 foi **interrompida e descartada após 7 minutos** (pasta `resultados/_abortada-*`, mantida para auditoria): a regra de deriva (seção 3) disparou já na primeira consulta (F1, 33%) porque a máquina estava executando a compilação do artigo e testes de outros agentes ao mesmo tempo, e repetir por deriva poderia dobrar a duração. **A R1 é, por desenho, a rodada com a máquina em uso; ela roda com `--sem-repetir-por-deriva`** (a deriva A1/A2 continua medida e gravada em `coleta_consulta.deriva_a1_a2`). A regra de repetição por deriva vale na **R2**, executada sem outra carga. As leituras lógicas, que sustentam a hipótese, não dependem dessa regra.

## 7. Alterações registradas depois dos resultados da R1 (26/10/2026), com seus motivos

Estas alterações **não** mudam hipótese, critérios, limiares nem a escolha de consultas; são correções de instrumento e análises adicionais **rotuladas como pós-hoc**.

1. **Defeito do microbenchmark de escrita (corrigido; dados de escrita da R1 e da R2-compat160 descartados).** O DML usava `UPDATE TOP (k)` e `DELETE TOP (k)` sem `ORDER BY`; o conjunto de linhas tocado depende do caminho de acesso, que muda quando o índice existe. Sintomas na tabela da R1: razões de bytes de log do `DELETE` abaixo de 1 (0,65 a 0,75) e razão 0,00 no `UPDATE` de `Users.Location`, cujas primeiras linhas têm `Location` nulo. Correção em `tcclab/escrita.py`: o alvo é um intervalo da chave clusterizada (`Id <= k-ésimo menor Id`), idêntico com e sem índice, e a expressão do `UPDATE` sempre altera o valor, inclusive em colunas anuláveis. O custo de escrita passa a vir da rodada dedicada `escrita_rodada.py` (17 índices do *framework* e 3 índices nativos distintos), que inclui o braço nativo, antes fora da medição de escrita. A R2-compat100 já usa o código corrigido; a R2-compat160 e a R1 usaram o antigo, e seus dados de escrita não entram no artigo.
2. **Iteração 2 pós-hoc de S2 e S3 (`iteracao2.py`).** Na R1, S2 (6,4%) não atingiu o critério e S3 (28,9%) passou por pouco; o índice nativo mostrou que o gargalo dominante era a varredura de `Posts` pelo filtro externo. A regra de iteração do *framework* manda voltar ao Diagnóstico quando não há melhoria significativa; a iteração 2 acrescenta o índice do filtro externo. **Não estava pré-registrada**; é exploratória e deve ser rotulada como tal no artigo. As conclusões principais usam só a prescrição da iteração 1.
3. **Diagnóstico e conjunto de validação:** ver seção 5. A execução com planos foi feita depois da R1, com o detector e o conjunto congelados por hash (coincidentes).

## 8. Execução registrada (26/10/2026)
- R1 (computador em uso) e R2 (máquina quieta), cada uma com compat 160 (quente e frio) e compat 100 (quente), sem falhas e sem consulta excluída; equivalência de resultado confirmada nas 14 consultas com reescrita; critérios e limiares **não foram alterados** depois da coleta.
- R2 repetiu por deriva F3 (compat 160) e C3 (compat 100), conforme a regra da seção 3; a tentativa descartada permanece gravada (`coleta_consulta.descartado_por_deriva`).
- A hipótese (redução mediana ≥ 20% nas leituras lógicas) foi confirmada no conjunto: 99,9%, Wilcoxon exato p < 0,001, Holm < 0,001; 16 de 17 consultas atingiram o limiar, e **S2 não atingiu (6,4%)**.
- Custo de escrita: rodada dedicada com o método corrigido (seção 7, item 1). Iteração 2 de S2/S3: pós-hoc (seção 7, item 2).
