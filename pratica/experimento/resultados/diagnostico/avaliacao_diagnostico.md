# Validação do instrumento: Diagnóstico (camada 1)

Gerado em 2026-10-25 por `experimento/avaliar_diagnostico.py`. sqlglot 30.21.0. 19 consultas do catálogo e 9 controles limpos. Offline (nenhuma consulta ao SQL Server).

## Planos usados

- Pasta de planos do catálogo: `/Users/douglasbarra/code/TCC/pratica/experimento/planos`; `indice.json` presente.
- Pasta de planos dos controles: `/Users/douglasbarra/code/TCC/pratica/experimento/planos_controles`; `indice.json` presente.
- Plano estimado: 42 casos com plano; 0 sem arquivo.
- Plano real: 42 casos com plano; 0 sem arquivo.

## Limiares usados (suposições a calibrar)

`p1_linhas_minimas=100000`, `p1_fracao_maxima=0.2`, `p1_fracao_alta=0.01`, `p1_leitura_minima=0.5`, `p2_execucoes_minimas=1000`, `p2_execucoes_altas=10000`, `p5_impacto_minimo=50.0`, `p5_impacto_alto=80.0`, `p6_execucoes_minimas=1000`, `p6_execucoes_altas=10000`, `s3_subconsultas_alta=2`.

## Resumo por modo

| Modo | Originais com AP detectável | Acertos | Confirmados | Só indício | Omissões | Reescritas sem resíduo do AP | Reescritas com resíduo no SQL | Controles sem achado de AP |
|---|---|---|---|---|---|---|---|---|
| só SQL | 14 | 14/14 (100%; IC95% de Wilson 78% a 100%) | 0 | 14 | 0 | 14/14 | 0/14 | 9/9 (100%; IC95% de Wilson 70% a 100%) |
| SQL + plano estimado | 19 | 19/19 (100%; IC95% de Wilson 83% a 100%) | 10 | 9 | 0 | 14/14 | 0/14 | 9/9 (100%; IC95% de Wilson 70% a 100%) |
| SQL + plano real | 19 | 19/19 (100%; IC95% de Wilson 83% a 100%) | 10 | 9 | 0 | 14/14 | 0/14 | 9/9 (100%; IC95% de Wilson 70% a 100%) |

*Acerto* = o antipadrão declarado aparece em algum achado (confirmado = SQL e plano concordam; indício = só um lado). Para AP4 (falta de índice) o SQL não é antipadrão: "confirmado" significa só que a coluna escaneada ou sugerida para índice é a mesma filtrada no SQL, um critério mais fraco que o de AP1 (função vista nos dois lados). *Resíduo* = o antipadrão declarado ainda aparece na reescrita. Intervalos de Wilson com n pequeno são largos: leia como ordem de grandeza.


## Casos: só SQL

| Consulta | AP declarado | Variante | Resultado | Regras que dispararam (* = confirmado) | Regras de outra classe |
|---|---|---|---|---|---|
| F1 | AP1 | original | acerto_indicio | S2 | - |
| F1 | AP1 | reescrita | reescrita_limpa | - | - |
| F2 | AP1 | original | acerto_indicio | S2 | - |
| F2 | AP1 | reescrita | reescrita_limpa | - | - |
| F3 | AP1 | original | acerto_indicio | S2 | - |
| F3 | AP1 | reescrita | reescrita_limpa | - | - |
| F4 | AP1 | original | acerto_indicio | S2 | - |
| F4 | AP1 | reescrita | reescrita_limpa | - | - |
| F5 | AP1 (controle) | original | acerto_indicio | S2 | - |
| F5 | AP1 (controle) | reescrita | reescrita_limpa | - | - |
| S1 | AP2 | original | acerto_indicio | S3 | - |
| S1 | AP2 | reescrita | reescrita_limpa | - | - |
| S2 | AP2 | original | acerto_indicio | S3 | - |
| S2 | AP2 | reescrita | reescrita_limpa | - | - |
| S3 | AP2 | original | acerto_indicio | S3 | - |
| S3 | AP2 | reescrita | reescrita_limpa | - | - |
| S4 | AP2 | original | acerto_indicio | S3 | - |
| S4 | AP2 | reescrita | reescrita_limpa | - | - |
| S5 | AP2 (controle) | original | acerto_indicio | S3 | - |
| S5 | AP2 (controle) | reescrita | reescrita_limpa | - | - |
| C1 | AP3 | original | acerto_indicio | S1 | - |
| C1 | AP3 | reescrita | reescrita_limpa | - | - |
| C2 | AP3 | original | acerto_indicio | S1 | - |
| C2 | AP3 | reescrita | reescrita_limpa | - | - |
| C3 | AP3 | original | acerto_indicio | S1 | - |
| C3 | AP3 | reescrita | reescrita_limpa | - | - |
| C4 | AP3 | original | acerto_indicio | S1 | - |
| C4 | AP3 | reescrita | reescrita_limpa | - | - |
| V1 | AP4 | original | fora_do_alcance | - | - |
| V2 | AP4 | original | fora_do_alcance | - | - |
| V3 | AP4 | original | fora_do_alcance | - | - |
| V4 | AP4 | original | fora_do_alcance | - | - |
| V5 | AP4 | original | fora_do_alcance | - | - |
| L1 | NENHUM | original | controle_limpo | - | - |
| L2 | NENHUM | original | controle_limpo | - | - |
| L3 | NENHUM | original | controle_limpo | - | - |
| L4 | NENHUM | original | controle_limpo | - | - |
| L5 | NENHUM | original | controle_limpo | - | - |
| L6 | NENHUM | original | controle_limpo | - | - |
| L7 | NENHUM | original | controle_limpo | - | - |
| L8 | NENHUM | original | controle_limpo | - | - |
| L9 | NENHUM | original | controle_limpo | - | - |

Fora do alcance só com SQL (AP4 exige plano): V1, V2, V3, V4, V5.

## Casos: SQL + plano estimado

| Consulta | AP declarado | Variante | Resultado | Regras que dispararam (* = confirmado) | Regras de outra classe |
|---|---|---|---|---|---|
| F1 | AP1 | original | acerto_confirmado | P1* P3* S2* | P1 |
| F1 | AP1 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| F2 | AP1 | original | acerto_confirmado | P1* P3* S2* | P1 |
| F2 | AP1 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| F3 | AP1 | original | acerto_confirmado | P1* P3* S2* | P1 |
| F3 | AP1 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| F4 | AP1 | original | acerto_confirmado | P3* S2* | - |
| F4 | AP1 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| F5 | AP1 (controle) | original | acerto_confirmado | P1* P3* P5* S2* | P1 P5 |
| F5 | AP1 (controle) | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| S1 | AP2 | original | acerto_indicio | P1* S3 | P1 |
| S1 | AP2 | reescrita | reescrita_limpa | P1* | P1 |
| S2 | AP2 | original | acerto_indicio | P1* P5* S3 | P1 P5 |
| S2 | AP2 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| S3 | AP2 | original | acerto_indicio | P1* P5* S3 | P1 P5 |
| S3 | AP2 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| S4 | AP2 | original | acerto_indicio | P1* S3 | P1 |
| S4 | AP2 | reescrita | reescrita_limpa | P1* | P1 |
| S5 | AP2 (controle) | original | acerto_indicio | P1* S3 | P1 |
| S5 | AP2 (controle) | reescrita | reescrita_limpa | P1* | P1 |
| C1 | AP3 | original | acerto_indicio | P1* P5* S1 | P1 P5 |
| C1 | AP3 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| C2 | AP3 | original | acerto_indicio | P1* P5* S1 | P1 P5 |
| C2 | AP3 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| C3 | AP3 | original | acerto_indicio | P1* P5* S1 | P1 P5 |
| C3 | AP3 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| C4 | AP3 | original | acerto_indicio | P1* P5* S1 | P1 P5 |
| C4 | AP3 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| V1 | AP4 | original | acerto_confirmado | P1* P5* | - |
| V2 | AP4 | original | acerto_confirmado | P1* P5* | - |
| V3 | AP4 | original | acerto_confirmado | P1* P5* | - |
| V4 | AP4 | original | acerto_confirmado | P1* P5* | - |
| V5 | AP4 | original | acerto_confirmado | P1* P5* | - |
| L1 | NENHUM | original | controle_limpo | - | - |
| L2 | NENHUM | original | controle_limpo | - | - |
| L3 | NENHUM | original | controle_limpo | - | - |
| L4 | NENHUM | original | controle_limpo | - | - |
| L5 | NENHUM | original | controle_limpo | - | - |
| L6 | NENHUM | original | controle_limpo | - | - |
| L7 | NENHUM | original | controle_limpo | - | - |
| L8 | NENHUM | original | controle_limpo | - | - |
| L9 | NENHUM | original | controle_limpo | - | - |

## Casos: SQL + plano real

| Consulta | AP declarado | Variante | Resultado | Regras que dispararam (* = confirmado) | Regras de outra classe |
|---|---|---|---|---|---|
| F1 | AP1 | original | acerto_confirmado | P1* P3* S2* | P1 |
| F1 | AP1 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| F2 | AP1 | original | acerto_confirmado | P1* P3* S2* | P1 |
| F2 | AP1 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| F3 | AP1 | original | acerto_confirmado | P1* P3* S2* | P1 |
| F3 | AP1 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| F4 | AP1 | original | acerto_confirmado | P3* S2* | - |
| F4 | AP1 | reescrita | reescrita_limpa | P5* | P5 |
| F5 | AP1 (controle) | original | acerto_confirmado | P1* P3* P5* S2* | P1 P5 |
| F5 | AP1 (controle) | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| S1 | AP2 | original | acerto_indicio | P1* S3 | P1 |
| S1 | AP2 | reescrita | reescrita_limpa | P1* | P1 |
| S2 | AP2 | original | acerto_indicio | P1* P5* S3 | P1 P5 |
| S2 | AP2 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| S3 | AP2 | original | acerto_indicio | P1* P5* S3 | P1 P5 |
| S3 | AP2 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| S4 | AP2 | original | acerto_indicio | P1* S3 | P1 |
| S4 | AP2 | reescrita | reescrita_limpa | P1* | P1 |
| S5 | AP2 (controle) | original | acerto_indicio | P1* S3 | P1 |
| S5 | AP2 (controle) | reescrita | reescrita_limpa | P1* | P1 |
| C1 | AP3 | original | acerto_indicio | P1* P5* S1 | P1 P5 |
| C1 | AP3 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| C2 | AP3 | original | acerto_indicio | P1* P5* S1 | P1 P5 |
| C2 | AP3 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| C3 | AP3 | original | acerto_indicio | P1* P5* S1 | P1 P5 |
| C3 | AP3 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| C4 | AP3 | original | acerto_indicio | P1* P5* S1 | P1 P5 |
| C4 | AP3 | reescrita | reescrita_limpa | P1* P5* | P1 P5 |
| V1 | AP4 | original | acerto_confirmado | P1* P5* | - |
| V2 | AP4 | original | acerto_confirmado | P1* P5* | - |
| V3 | AP4 | original | acerto_confirmado | P1* P5* | - |
| V4 | AP4 | original | acerto_confirmado | P1* P5* | - |
| V5 | AP4 | original | acerto_confirmado | P1* P5* | - |
| L1 | NENHUM | original | controle_limpo | - | - |
| L2 | NENHUM | original | controle_limpo | - | - |
| L3 | NENHUM | original | controle_limpo | - | - |
| L4 | NENHUM | original | controle_limpo | - | - |
| L5 | NENHUM | original | controle_limpo | - | - |
| L6 | NENHUM | original | controle_limpo | - | - |
| L7 | NENHUM | original | controle_limpo | - | - |
| L8 | NENHUM | original | controle_limpo | - | - |
| L9 | NENHUM | original | controle_limpo | - | - |

## Matriz de confusão por regra

VP/FN: regra deveria disparar (original com o antipadrão da regra). FP/VN: não deveria (reescrita do mesmo antipadrão e controles limpos). "Não rotulados": consultas de outra classe, não pontuadas. **Leitura dos FN das regras de plano:** o rótulo vem do SQL, mas a regra de plano só vê o que o plano mostra; na linha de base (sem índices não clusterizados) não há Key Lookup (P2) e o otimizador pode ter trocado a subconsulta por junção hash (P6). Esses FN dizem que o plano não manifesta o padrão, não que a regra errou; o antipadrão ainda é pego pela regra de SQL.


**só SQL**

| Regra | AP | VP (confirm.) | FN | FP | VN | Não rot. disparou/quieta | Sensib. | Especif. | Precisão |
|---|---|---|---|---|---|---|---|---|---|
| S1 | AP3 | 4 (0) | 0 | 0 | 13 | 0/25 | 1,0 | 1,0 | 1,0 |
| S2 | AP1 | 5 (0) | 0 | 0 | 14 | 0/23 | 1,0 | 1,0 | 1,0 |
| S3 | AP2 | 5 (0) | 0 | 0 | 14 | 0/23 | 1,0 | 1,0 | 1,0 |

**SQL + plano estimado**

| Regra | AP | VP (confirm.) | FN | FP | VN | Não rot. disparou/quieta | Sensib. | Especif. | Precisão |
|---|---|---|---|---|---|---|---|---|---|
| S1 | AP3 | 4 (0) | 0 | 0 | 13 | 0/25 | 1,0 | 1,0 | 1,0 |
| S2 | AP1 | 5 (5) | 0 | 0 | 14 | 0/23 | 1,0 | 1,0 | 1,0 |
| S3 | AP2 | 5 (0) | 0 | 0 | 14 | 0/23 | 1,0 | 1,0 | 1,0 |
| P1 | AP4 | 5 (5) | 0 | 0 | 9 | 27/1 | 1,0 | 1,0 | 1,0 |
| P2 | AP3 | 0 (0) | 4 | 0 | 13 | 0/25 | 0,0 | 1,0 |  |
| P3 | AP1 | 5 (5) | 0 | 0 | 14 | 0/23 | 1,0 | 1,0 | 1,0 |
| P4 | AP1 | 0 (0) | 0 | 0 | 14 | 0/28 |  | 1,0 |  |
| P5 | AP4 | 5 (5) | 0 | 0 | 9 | 18/10 | 1,0 | 1,0 | 1,0 |
| P6 | AP2 | 0 (0) | 5 | 0 | 14 | 0/23 | 0,0 | 1,0 |  |

**SQL + plano real**

| Regra | AP | VP (confirm.) | FN | FP | VN | Não rot. disparou/quieta | Sensib. | Especif. | Precisão |
|---|---|---|---|---|---|---|---|---|---|
| S1 | AP3 | 4 (0) | 0 | 0 | 13 | 0/25 | 1,0 | 1,0 | 1,0 |
| S2 | AP1 | 5 (5) | 0 | 0 | 14 | 0/23 | 1,0 | 1,0 | 1,0 |
| S3 | AP2 | 5 (0) | 0 | 0 | 14 | 0/23 | 1,0 | 1,0 | 1,0 |
| P1 | AP4 | 5 (5) | 0 | 0 | 9 | 26/2 | 1,0 | 1,0 | 1,0 |
| P2 | AP3 | 0 (0) | 4 | 0 | 13 | 0/25 | 0,0 | 1,0 |  |
| P3 | AP1 | 5 (5) | 0 | 0 | 14 | 0/23 | 1,0 | 1,0 | 1,0 |
| P4 | AP1 | 0 (0) | 0 | 0 | 14 | 0/28 |  | 1,0 |  |
| P5 | AP4 | 5 (5) | 0 | 0 | 9 | 18/10 | 1,0 | 1,0 | 1,0 |
| P6 | AP2 | 0 (0) | 5 | 0 | 14 | 0/23 | 0,0 | 1,0 |  |

## Declarado × detectado (originais)


**só SQL**

| Declarado | n | detectou AP1 | AP2 | AP3 | AP4 | sem achado de AP |
|---|---|---|---|---|---|---|
| AP1 | 5 | 5 | 0 | 0 | 0 | 0 |
| AP2 | 5 | 0 | 5 | 0 | 0 | 0 |
| AP3 | 4 | 0 | 0 | 4 | 0 | 0 |
| AP4 | 5 | 0 | 0 | 0 | 0 | 5 |
| NENHUM | 9 | 0 | 0 | 0 | 0 | 9 |

**SQL + plano estimado**

| Declarado | n | detectou AP1 | AP2 | AP3 | AP4 | sem achado de AP |
|---|---|---|---|---|---|---|
| AP1 | 5 | 5 | 0 | 0 | 4 | 0 |
| AP2 | 5 | 0 | 5 | 0 | 5 | 0 |
| AP3 | 4 | 0 | 0 | 4 | 4 | 0 |
| AP4 | 5 | 0 | 0 | 0 | 5 | 0 |
| NENHUM | 9 | 0 | 0 | 0 | 0 | 9 |

**SQL + plano real**

| Declarado | n | detectou AP1 | AP2 | AP3 | AP4 | sem achado de AP |
|---|---|---|---|---|---|---|
| AP1 | 5 | 5 | 0 | 0 | 4 | 0 |
| AP2 | 5 | 0 | 5 | 0 | 5 | 0 |
| AP3 | 4 | 0 | 0 | 4 | 4 | 0 |
| AP4 | 5 | 0 | 0 | 0 | 5 | 0 |
| NENHUM | 9 | 0 | 0 | 0 | 0 | 9 |

## Limites desta avaliação

- Os rótulos vêm do catálogo escrito por quem também escreveu o detector: não é avaliação independente. As consultas semeadas são poucas (uma ou duas por variação) e foram desenhadas para exibir o antipadrão; o desempenho em código real de terceiros **não** foi medido.
- Os planos são da linha de base (sem índices não clusterizados) e de uma única compilação cada, no SQL Server 2022 do experimento; outras versões, estatísticas ou níveis de compatibilidade podem gerar planos diferentes.
- Limiares são suposições (ver `Limiares`); esta avaliação não os calibrou. Para ver o efeito, repita com `--limiar nome=valor`.
- F5 e S5 são controles de efeito do catálogo (o otimizador pode já tratá-los): o SQL contém o padrão, então contam como positivos do detector.
- **Avaliação na amostra de desenvolvimento**: as consultas do catálogo e dos controles foram vistas enquanto as regras eram escritas e ajustadas (por exemplo, o filtro PROBE e a contagem de execuções do lado interno vieram de planos desta própria coleta). Acerto de 100% nas regras de SQL é, portanto, o esperado e **não mede generalização**; serve como teste de regressão e de coerência entre catálogo e detector.
- P4 (PlanAffectingConvert) não tem positivo rotulável no catálogo (nenhuma consulta com tipos incompatíveis); P2 e P6 só aparecem em planos de variantes com índice (fora desta avaliação, que usa a linha de base); ver `knowledge/24-diagnostico.md`.
