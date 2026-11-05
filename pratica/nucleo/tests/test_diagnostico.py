"""Unidade: Diagnostico (camada 1). Offline: SQL do catalogo + planos XML salvos (tests/fixtures/dx_*.sqlplan, ver dx_ORIGEM.txt).

Planos "derivados" (predicado trocado, RunTimeInformation removido) sao montados aqui e rotulados como derivados: testam a
estrutura que a regra le, nao provam o XML exato que o servidor emitiria.
"""
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from tcclab import catalogo, diagnostico  # noqa: E402
from tcclab.diagnostico import Limiares, diagnosticar  # noqa: E402

FX = Path(__file__).parent / "fixtures"
EXP = Path(__file__).resolve().parents[2] / "experimento"
CAT = catalogo.carregar(EXP / "catalogo.toml")
CTL = catalogo.carregar(EXP / "controles_limpos.toml")
SQL_C1 = CAT.por_id("C1").sql_original
SQL_M0 = "SELECT COUNT(*), SUM(valor) FROM dbo.m0_sanidade WHERE cliente = 42"


def plano(nome: str) -> str:
    return (FX / nome).read_text(encoding="utf-8")


def regras(d, nivel=None):
    return sorted(a.regra for a in d.achados if nivel is None or a.nivel == nivel)


# ------------------------------------------------------------------------------------------------ SQL do catalogo
SEMEADAS = [c for c in CAT.consultas if c.antipadrao in ("AP1", "AP2", "AP3")]
REGRA_DO_AP = {"AP1": "S2", "AP2": "S3", "AP3": "S1"}


@pytest.mark.parametrize("c", SEMEADAS, ids=lambda c: c.id)
def test_sql_do_catalogo_detecta_o_antipadrao_declarado(c):
    d = diagnosticar(c.sql_original)
    assert d.sql_analisavel and REGRA_DO_AP[c.antipadrao] in regras(d)
    assert set(regras(d)) == {REGRA_DO_AP[c.antipadrao]}   # e nada de outra classe
    assert d.plano == "nenhum" and all(a.nivel == "indicio" for a in d.achados)   # sem plano nada e "confirmado"


@pytest.mark.parametrize("c", [c for c in SEMEADAS if c.sql_reescrita], ids=lambda c: c.id)
def test_sql_da_reescrita_nao_tem_mais_o_antipadrao(c):
    assert diagnosticar(c.sql_reescrita).achados == []


@pytest.mark.parametrize("c", [c for c in CAT.consultas if c.antipadrao == "AP4"] + CTL.consultas, ids=lambda c: c.id)
def test_sql_de_ap4_e_dos_controles_limpos_sem_achado_de_sql(c):
    assert diagnosticar(c.sql_original).achados == []   # AP4 exige o plano; os controles nao tem antipadrao


def test_sem_plano_o_resultado_diz_que_so_o_sql_rodou():
    d = diagnosticar(CAT.por_id("F1").sql_original)
    assert d.plano == "nenhum" and d.regras_executadas == ["S1", "S2", "S3"]
    assert set(d.regras_ignoradas) == {"P1", "P2", "P3", "P4", "P5", "P6"}
    assert all("sem plano" in m for m in d.regras_ignoradas.values())
    assert "NAO executada" in diagnostico.formatar(d)


def test_trecho_e_linha_do_sql_original():
    f1 = diagnosticar(CAT.por_id("F1").sql_original)
    assert sorted(a.trecho_sql for a in f1.achados) == ["MONTH(CreationDate) = 8", "YEAR(CreationDate) = 2008"]
    assert {a.linha_sql for a in f1.achados} == {1} and all(a.colunas == ("CreationDate",) and a.tabelas == ("Posts",) for a in f1.achados)
    s1 = diagnosticar(CAT.por_id("S1").sql_original)
    assert [a.linha_sql for a in s1.achados] == [2, 3, 4]    # cada subconsulta na sua linha
    assert s1.achados[1].trecho_sql.startswith("(SELECT MAX(p.Score) FROM dbo.Posts p")
    assert all(a.gravidade == "alta" for a in s1.achados)    # 3 subconsultas sobre Posts
    assert diagnosticar(CAT.por_id("S4").sql_original).achados[0].gravidade == "media"   # uma so


def test_funcao_em_f2_mesmo_com_colacao_que_ignora_maiusculas():
    d = diagnosticar(CAT.por_id("F2").sql_original)
    assert regras(d) == ["S2"] and d.achados[0].trecho_sql == "UPPER(DisplayName) = 'JON SKEET'"


# ------------------------------------------------------------------------------------------------ casos de borda de SQL
@pytest.mark.parametrize("sql, esperado", [
    ("SELECT a FROM t WHERE LEFT(name, 3) = 'abc'", ["S2"]),
    ("SELECT a FROM t WHERE ISNULL(a, 0) = 0", ["S2"]),
    ("SELECT a FROM t WHERE a + 1 = 5", ["S2"]),
    ("SELECT a FROM t WHERE CAST(a AS varchar(10)) = '5'", ["S2"]),
    ("SELECT a FROM t WHERE CONVERT(varchar(10), d, 120) = '2010-01-01'", ["S2"]),
    ("SELECT a FROM t WHERE DATEPART(year, d) = 2008", ["S2"]),
    ("SELECT a FROM t WHERE LOWER(a) LIKE 'x%'", ["S2"]),
    ("SELECT a FROM t WHERE YEAR(d) IN (2008, 2009)", ["S2"]),
    ("SELECT a FROM t WHERE dbo.fn(x) = 1", ["S2"]),
    ("SELECT a.x FROM a JOIN b ON UPPER(a.x) = b.y", ["S2"]),
    ("UPDATE t SET a = 1 WHERE YEAR(d) = 2008", ["S2"]),
    ("SELECT a FROM t WHERE N'abc' = UPPER(b)", ["S2"]),
    ("SELECT t.*, u.b FROM t JOIN u ON u.k = t.k", ["S1"]),
    ("SELECT TOP (10) * FROM t ORDER BY a", ["S1"]),
    ("SELECT a, (SELECT TOP 1 b FROM u WHERE u.k = t.k ORDER BY b DESC) FROM t", ["S3"]),
    # sem achado: o que NAO e antipadrao (ou esta fora do escopo declarado)
    ("SELECT a FROM t WHERE col = 5 AND x IN (1, 2) AND n LIKE 'abc%' AND y = @p AND z BETWEEN 1 AND 5 AND w IS NULL", []),
    ("SELECT a FROM t WHERE d >= DATEADD(day, -7, GETDATE()) AND e >= CAST('20200101' AS date)", []),
    ("SELECT g, COUNT(*) FROM t GROUP BY g HAVING COUNT(*) > 5 AND SUM(x) > 10", []),
    ("SELECT YEAR(d), a FROM t GROUP BY YEAR(d) ORDER BY YEAR(d)", []),
    ("SELECT a FROM t WHERE EXISTS (SELECT * FROM u WHERE u.k = t.k)", []),
    ("SELECT a FROM t WHERE k IN (SELECT x FROM u WHERE u.k = t.k)", []),
    ("SELECT a FROM t WHERE col = (SELECT MAX(x) FROM u)", []),
    ("SELECT a FROM t CROSS APPLY (SELECT COUNT(*) AS n FROM u WHERE u.k = t.k) z", []),
    ("SELECT a FROM t WHERE a COLLATE Latin1_General_CS_AS = 'x'", []),
])
def test_s1_s2_s3_casos_de_borda(sql, esperado):
    assert regras(diagnosticar(sql)) == sorted(esperado)


def test_select_estrela_sobre_cte_ou_derivada_e_gravidade_baixa():
    for sql in ("WITH c AS (SELECT Id FROM t) SELECT * FROM c", "SELECT * FROM (SELECT Id FROM t) x"):
        d = diagnosticar(sql)
        assert regras(d) == ["S1"] and d.achados[0].gravidade == "baixa"


@pytest.mark.parametrize("sql", ["", "SELECT * FROM t WHERE (", "SELEC * FRM x", "EXEC sp_who", "   "])
def test_texto_nao_analisavel_nao_vira_sem_problemas(sql):
    d = diagnosticar(sql)          # nao lanca
    assert not d.sql_analisavel and d.motivo_sql
    assert d.achados == [] and {"S1", "S2", "S3"} <= set(d.regras_ignoradas)
    assert all("não analisável" in m for r, m in d.regras_ignoradas.items() if r[0] == "S")


def test_lote_com_declare_analisa_o_select_e_avisa():
    d = diagnosticar("DECLARE @x int = 1; SELECT * FROM t WHERE a = @x")
    assert d.sql_analisavel and regras(d) == ["S1"] and any("ignorada" in a for a in d.avisos)


def test_sem_sqlglot_so_as_regras_de_plano(monkeypatch):
    monkeypatch.setattr(diagnostico, "sqlglot", None)
    d = diagnosticar(SQL_C1, plano("dx_c1_original_varredura.sqlplan"))
    assert not d.sql_analisavel and "sqlglot" in d.motivo_sql
    assert regras(d) == ["P1", "P5"] and {"S1", "S2", "S3"} <= set(d.regras_ignoradas)


# ------------------------------------------------------------------------------------------------ planos reais
def test_c1_baseline_varredura_p1_p5_sem_p3_e_s1_so_indicio():
    d = diagnosticar(SQL_C1, plano("dx_c1_original_varredura.sqlplan"))
    assert d.plano == "real" and d.regras_ignoradas == {}
    assert regras(d) == ["P1", "P5", "S1"]
    p1, p5, s1 = d.da_regra("P1")[0], d.da_regra("P5")[0], d.da_regra("S1")[0]
    assert p1.detalhes["linhas_saida"] == 11354 and p1.detalhes["cardinalidade"] == 3729200 and p1.gravidade == "alta"
    assert p1.no_plano == 0 and p1.operador == "Clustered Index Scan" and "dbo.Posts" in p1.evidencia
    assert p5.detalhes["chave"] == ["OwnerUserId"] and p5.detalhes["impacto"] > 99
    assert (p1.nivel, p5.nivel) == ("confirmado", "confirmado")   # OwnerUserId tambem e o filtro do SQL
    assert s1.nivel == "indicio" and any("Key Lookup" in n for n in s1.notas)   # SELECT * sem lookup: o plano nao corrobora
    # CONVERT_IMPLICIT(int,[@1],0) converte o PARAMETRO, nao a coluna: nao e P3
    assert "CONVERT_IMPLICIT" in p1.evidencia and not d.da_regra("P3")


def test_c1_com_indice_nao_cobrindo_key_lookup_confirma_select_estrela():
    d = diagnosticar(SQL_C1, plano("dx_c1_original_lookup.sqlplan"))
    assert regras(d) == ["P2", "S1"] and regras(d, "confirmado") == ["P2", "S1"]
    p2, s1 = d.da_regra("P2")[0], d.da_regra("S1")[0]
    assert p2.detalhes["execucoes"] == 11354 and p2.detalhes["leituras_logicas"] == 19658 and p2.no_plano == 4
    assert p2.operador == "Key Lookup" and p2.gravidade == "alta" and p2.tabelas == ("Posts",)
    assert p2.corrobora == (s1.id,) and s1.corrobora == (p2.id,)
    assert "Nó 4" in s1.evidencia and "SELECT *" in p2.evidencia   # a evidencia cita os dois lados, sem repeticao circular
    assert p2.evidencia.count("concorda") == 1


def test_c1_reescrita_com_indice_cobrindo_nao_tem_achado():
    d = diagnosticar(CAT.por_id("C1").sql_reescrita, plano("dx_c1_reescrita_cobrindo.sqlplan"))
    assert d.achados == [] and d.regras_ignoradas == {}


def test_varredura_em_m0_real_e_estimada():
    real = diagnosticar(SQL_M0, plano("plano_real_scan.sqlplan"))
    est = diagnosticar(SQL_M0, plano("plano_estimado_scan.sqlplan"))
    assert (real.plano, est.plano) == ("real", "estimado") and regras(real) == regras(est) == ["P1"]
    assert real.achados[0].detalhes["base"] == "real" and real.achados[0].detalhes["linhas_saida"] == 78
    assert est.achados[0].detalhes["base"] == "estimado" and abs(est.achados[0].detalhes["linhas_saida"] - 64.0621) < 1e-3
    assert "estima" in est.achados[0].evidencia
    assert diagnosticar(SQL_M0, plano("plano_real_seek.sqlplan")).achados == []   # com indice: seek, nada a acusar


@pytest.mark.parametrize("qid, funcao, tabela", [("F1", "datepart", "Posts"), ("F2", "upper", "Users"), ("F3", "datediff", "Votes"), ("F5", "CONVERT/CAST", "Votes")])
def test_funcao_no_predicado_de_plano_real_confirma_s2(qid, funcao, tabela):
    arq = {"F1": "dx_f1_original_real", "F2": "dx_f2_original_real", "F3": "dx_f3_original_real", "F5": "dx_f5_original_real"}[qid]
    d = diagnosticar(CAT.por_id(qid).sql_original, plano(f"{arq}.sqlplan"))
    s2, p3 = d.da_regra("S2")[0], d.da_regra("P3")[0]
    assert s2.confirmado and p3.confirmado and s2.corrobora == (p3.id,) and p3.operador == "Clustered Index Scan"
    assert funcao.lower() in p3.evidencia.lower() and p3.tabelas == (tabela,) and p3.gravidade == "alta"
    assert d.antipadroes("confirmado") >= {"AP1"}


def test_f3_cita_tambem_a_conversao_implicita_sobre_a_coluna():
    p3 = diagnosticar(CAT.por_id("F3").sql_original, plano("dx_f3_original_real.sqlplan")).da_regra("P3")[0]
    assert {u["tipo"] for u in p3.detalhes["usos"]} == {"funcao", "conversao_implicita"}


def test_f1_reescrita_sem_funcao_mas_ainda_varre_a_tabela():
    d = diagnosticar(CAT.por_id("F1").sql_reescrita, plano("dx_f1_reescrita_real.sqlplan"))
    assert regras(d) == ["P1", "P5"] and not d.da_regra("P3") and not d.da_regra("S2")   # AP1 some; falta o indice (AP4)


def test_p1_estimado_pode_divergir_do_real_quando_a_estimativa_erra():
    """F4 (reescrita): o real devolve 58% das linhas (nao e acusado); o estimado, que supoe 0,53%, aciona P1. Achado de metodo."""
    sql = CAT.por_id("F4").sql_reescrita
    real = diagnosticar(sql, plano("dx_f4_reescrita_real.sqlplan"))
    est = diagnosticar(sql, plano("dx_f4_reescrita_estimado.sqlplan"))
    assert "P1" not in regras(real) and "P1" in regras(est)


def test_v3_ap4_so_com_plano_e_confirmado_pelo_filtro_do_sql():
    sql = CAT.por_id("V3").sql_original
    assert diagnosticar(sql).achados == []
    d = diagnosticar(sql, plano("dx_v3_original_real.sqlplan"))
    assert regras(d, "confirmado") == ["P1", "P5"] and d.antipadroes() == {"AP4"}
    assert d.da_regra("P1")[0].detalhes["linhas_saida"] == 2821 and d.nivel_do_antipadrao("AP4") == "confirmado"


@pytest.mark.parametrize("nome", ["l4", "l5", "l7", "l8"])
def test_varredura_legitima_e_plano_de_controle_limpo_nao_sao_acusados(nome):
    """L4: varredura de 10 M de linhas SEM filtro; L8: tabela minuscula; L7: TOP; L5: junção por seeks."""
    c = CTL.por_id(nome.upper())
    d = diagnosticar(c.sql_original, plano(f"dx_{nome}_original_real.sqlplan"))
    assert d.achados == [] and d.regras_ignoradas == {}


def test_s1_decorrelacionado_pelo_otimizador_fica_so_indicio_de_sql():
    d = diagnosticar(CAT.por_id("S1").sql_original, plano("dx_s1_original_hash.sqlplan"))
    s3 = d.da_regra("S3")
    assert len(s3) == 3 and all(a.nivel == "indicio" and any("Nested Loops" in n for n in a.notas) for a in s3)
    assert not d.da_regra("P6") and not d.da_regra("P3")      # PROBE (bitmap) nao e funcao sobre coluna nem filtro do autor
    assert [a.detalhes["fracao"] < 0.02 for a in d.da_regra("P1")] == [True]   # so a varredura de Users (Reputation) e acusada
    s5 = diagnosticar(CAT.por_id("S5").sql_original, plano("dx_s5_original_real.sqlplan"))
    assert regras(s5, "confirmado") == ["P1"] and [a.nivel for a in s5.da_regra("S3")] == ["indicio"]


def test_s1_com_indice_cobrindo_vira_nested_loops_correlacionado_e_confirma():
    d = diagnosticar(CAT.por_id("S1").sql_original, plano("dx_s1_original_indice_cobrindo.sqlplan"))
    p6 = d.da_regra("P6")
    assert len(p6) == 3 and all(a.confirmado and a.detalhes["execucoes_interno"] == 4020 and a.tabelas == ("Posts",) for a in p6)
    assert all(a.confirmado for a in d.da_regra("S3")) and d.nivel_do_antipadrao("AP2") == "confirmado"


def test_p6_tambem_acusa_juncao_executada_como_apply_limite_conhecido():
    """Reescrita (JOIN + GROUP BY) + indice nao cobrindo: o plano roda agregacao por linha externa. P6 e so plano: indicio."""
    d = diagnosticar(CAT.por_id("S1").sql_reescrita, plano("dx_s1_reescrita_indice_nao_cobrindo.sqlplan"))
    assert {a.regra for a in d.achados if a.antipadrao == "AP2"} == {"P6"} and d.nivel_do_antipadrao("AP2") == "indicio"
    assert d.da_regra("P2")[0].detalhes["execucoes"] > 1_000_000 and any("não usa SELECT *" in n for n in d.da_regra("P2")[0].notas)


def test_s1_reescrita_hash_nao_tem_achado_de_ap2():
    d = diagnosticar(CAT.por_id("S1").sql_reescrita, plano("dx_s1_reescrita_hash.sqlplan"))
    assert "AP2" not in d.antipadroes()


# ------------------------------------------------------------------------------------------------ planos derivados (sinteticos)
PRED = re.compile(r"<Predicate>.*?</Predicate>", re.S)
COL = '<ColumnReference Database="[tcc_lab]" Schema="[dbo]" Table="[m0_sanidade]" Column="cliente"></ColumnReference>'


def derivado(predicado: str | None, ancora_aviso: str = "") -> str:
    """DERIVADO do plano real de m0_sanidade: predicado da varredura trocado a mao (ou removido), avisos injetados."""
    xml = plano("plano_real_scan.sqlplan")
    xml = PRED.sub(predicado or "", xml)
    return xml.replace('<RelOp NodeId="0"', ancora_aviso + '<RelOp NodeId="0"', 1) if ancora_aviso else xml


def pred_xml(texto: str, corpo: str) -> str:
    return f'<Predicate><ScalarOperator ScalarString="{texto}">{corpo}</ScalarOperator></Predicate>'


def test_derivado_isnull_sobre_coluna_vira_p3_e_confirma_s2():
    corpo = (f'<Compare CompareOp="EQ"><ScalarOperator><Intrinsic FunctionName="isnull"><ScalarOperator><Identifier>{COL}</Identifier></ScalarOperator>'
             '<ScalarOperator><Const ConstValue="(0)"></Const></ScalarOperator></Intrinsic></ScalarOperator><ScalarOperator><Const ConstValue="(5)"></Const>'
             '</ScalarOperator></Compare>')
    sql = "SELECT COUNT(*), SUM(valor) FROM dbo.m0_sanidade WHERE ISNULL(cliente, 0) = 5"
    d = diagnosticar(sql, derivado(pred_xml("isnull([tcc_lab].[dbo].[m0_sanidade].[cliente],(0))=(5)", corpo)))
    assert {"P3", "S2"} <= set(regras(d, "confirmado")) and d.da_regra("P3")[0].gravidade == "alta"   # P1 tambem dispara (varredura seletiva)


def test_derivado_conversao_implicita_sobre_a_coluna_e_p3_media_mas_sobre_o_parametro_nao():
    param = '<ScalarOperator><Identifier><ColumnReference Column="@1"></ColumnReference></Identifier></ScalarOperator>'
    sobre_coluna = (f'<Compare CompareOp="EQ"><ScalarOperator><Convert DataType="bigint" Style="0" Implicit="1"><ScalarOperator><Identifier>{COL}</Identifier>'
                    f'</ScalarOperator></Convert></ScalarOperator>{param}</Compare>')
    d = diagnosticar(SQL_M0, derivado(pred_xml("CONVERT_IMPLICIT(bigint,[tcc_lab].[dbo].[m0_sanidade].[cliente],0)=[@1]", sobre_coluna)))
    assert regras(d) == ["P1", "P3"] and d.da_regra("P3")[0].gravidade == "media"
    assert d.da_regra("P3")[0].detalhes["usos"][0]["tipo"] == "conversao_implicita"
    sobre_param = (f'<Compare CompareOp="EQ"><ScalarOperator><Identifier>{COL}</Identifier></ScalarOperator><ScalarOperator><Convert DataType="int" Style="0" '
                   f'Implicit="1">{param}</Convert></ScalarOperator></Compare>')
    assert "P3" not in regras(diagnosticar(SQL_M0, derivado(pred_xml("[cliente]=CONVERT_IMPLICIT(int,[@1],0)", sobre_param))))


def test_derivado_varredura_sem_predicado_nao_e_acusada():
    d = diagnosticar("SELECT COUNT(*), SUM(valor) FROM dbo.m0_sanidade", derivado(None))
    assert d.achados == []


def test_derivado_avisos_do_plano_p4():
    avisos = ('<Warnings><PlanAffectingConvert ConvertIssue="Seek Plan" Expression="CONVERT_IMPLICIT(nvarchar(4000),[tcc_lab].[dbo].[m0_sanidade].[cliente],0)">'
              '</PlanAffectingConvert></Warnings><Warnings NoJoinPredicate="1"></Warnings>')
    sql = "SELECT COUNT(*), SUM(valor) FROM dbo.m0_sanidade WHERE CAST(cliente AS nvarchar(50)) = N'42'"
    d = diagnosticar(sql, derivado(None, avisos))
    p4 = d.da_regra("P4")
    conv = next(a for a in p4 if a.antipadrao == "AP1")
    assert conv.gravidade == "alta" and conv.tabelas == ("m0_sanidade",) and conv.colunas == ("cliente",)
    assert conv.confirmado and d.da_regra("S2")[0].confirmado          # a coluna do SQL aparece na expressao do aviso
    info = next(a for a in p4 if a.antipadrao is None)
    assert info.gravidade == "baixa" and info.detalhes["aviso"] == "NoJoinPredicate"


def sem_contadores_reais(xml: str) -> str:
    """DERIVADO: remove RunTimeInformation, o que deixa o plano com a cara de um plano estimado."""
    return re.sub(r"<RunTimeInformation>.*?</RunTimeInformation>", "", xml, flags=re.S)


def test_derivado_estimado_usa_linhas_do_lado_externo_como_execucoes():
    est = sem_contadores_reais(plano("dx_c1_original_lookup.sqlplan"))
    d = diagnosticar(SQL_C1, est)
    assert d.plano == "estimado"
    p2 = d.da_regra("P2")[0]
    assert p2.detalhes["execucoes"] == 11354 and p2.detalhes["base"] == "estimado" and p2.confirmado
    est6 = diagnosticar(CAT.por_id("S1").sql_original, sem_contadores_reais(plano("dx_s1_original_indice_cobrindo.sqlplan")))
    assert len(est6.da_regra("P6")) == 3 and est6.da_regra("P6")[0].detalhes["base"] == "estimado"


def test_limiares_configuraveis():
    xml, base = plano("dx_c1_original_lookup.sqlplan"), Limiares()
    assert diagnosticar(SQL_C1, xml, replace(base, p2_execucoes_minimas=20_000)).da_regra("P2") == []
    assert diagnosticar(SQL_C1, xml, replace(base, p2_execucoes_altas=50_000)).da_regra("P2")[0].gravidade == "media"
    scan = plano("dx_c1_original_varredura.sqlplan")
    assert diagnosticar(SQL_C1, scan, replace(base, p1_linhas_minimas=10_000_000)).da_regra("P1") == []      # tabela "pequena" para o limiar
    assert diagnosticar(SQL_C1, scan, replace(base, p1_fracao_maxima=0.001)).da_regra("P1") == []           # 0,30% > 0,1%
    assert diagnosticar(SQL_C1, scan, replace(base, p5_impacto_minimo=99.99999)).da_regra("P5") == []
    assert diagnosticar(SQL_C1, scan, base).limiares == base


# ------------------------------------------------------------------------------------------------ robustez
def test_plano_xml_invalido_nao_lanca_e_e_registrado():
    d = diagnosticar(SQL_C1, "<ShowPlanXML><nao fecha")
    assert d.plano == "invalido" and regras(d) == ["S1"] and "plano inválido" in d.regras_ignoradas["P1"]
    assert diagnosticar(SQL_C1, "<ShowPlanXML xmlns='http://schemas.microsoft.com/sqlserver/2004/07/showplan'></ShowPlanXML>").plano == "invalido"


def test_plano_de_outra_consulta_gera_aviso():
    d = diagnosticar("SELECT Id FROM dbo.Users WHERE Id = 1", plano("dx_c1_original_varredura.sqlplan"))
    assert any("outra consulta" in a for a in d.avisos)


def test_serializacao_e_texto():
    d = diagnosticar(SQL_C1, plano("dx_c1_original_lookup.sqlplan"))
    j = json.loads(d.para_json())
    assert j["plano"] == "real" and {a["regra"] for a in j["achados"]} == {"P2", "S1"} and j["limiares"]["p1_linhas_minimas"] == 100_000
    texto = diagnostico.formatar(d)
    assert "P2-1" in texto and "confirmado" in texto and "explicação" in texto


def test_cada_regra_tem_antipadrao_do_catalogo_e_id_estavel():
    assert {r: a for r, a in diagnostico.ANTIPADRAO_DA_REGRA.items()} == {
        "S1": "AP3", "S2": "AP1", "S3": "AP2", "P1": "AP4", "P2": "AP3", "P3": "AP1", "P4": "AP1", "P5": "AP4", "P6": "AP2"}
    d = diagnosticar(CAT.por_id("F1").sql_original, plano("dx_f1_original_real.sqlplan"))
    assert len({a.id for a in d.achados}) == len(d.achados) and {a.id for a in d.da_regra("S2")} == {"S2-1", "S2-2"}
