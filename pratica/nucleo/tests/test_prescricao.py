"""Unidade: Prescricao (camada 3). Offline: SQL do catalogo + planos REAIS salvos (tests/fixtures/pr_*.sqlplan; ver pr_ORIGEM.txt),
esquema REAL (pr_schema_so2010.json) e Conexao falsa. Casos SINTETICOS e esquemas DERIVADOS sao rotulados no proprio teste.

As regras foram escritas olhando os casos do catalogo: os testes sobre o catalogo sao REGRESSAO, nao prova de generalizacao
(knowledge/26, secao 7). Os sinteticos exercitam bordas que o catalogo nao tem.
"""
import re
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from conexao_falsa import ConexaoFalsa, lote_hash  # noqa: E402
from tcclab import catalogo, schema  # noqa: E402
from tcclab.analise_indices import LIMIARES_PADRAO  # noqa: E402
from tcclab.prescricao import ConfigPrescricao, formatar, gerar_reescritas, prescrever, verificar_candidatas  # noqa: E402
from tcclab.schema import DensidadeEsq, EsquemaBanco, Indice  # noqa: E402

FX = Path(__file__).parent / "fixtures"
EXP = Path(__file__).resolve().parents[2] / "experimento"
CAT = catalogo.carregar(EXP / "catalogo.toml")
SO = schema.carregar(FX / "pr_schema_so2010.json")


def real(qid: str) -> str:
    return (FX / f"pr_{qid}_real.sqlplan").read_text(encoding="utf-8")


def estimado(qid: str) -> str:
    return (FX / f"pr_{qid}_estimado.sqlplan").read_text(encoding="utf-8")


def sql(qid: str) -> str:
    return CAT.por_id(qid).sql_original


def colunas_cat(qid: str) -> list[str] | None:
    """Colunas da reescrita do catalogo (CIRCULAR: so o autor sabe quais colunas a aplicacao usa)."""
    c = CAT.por_id(qid)
    if c.antipadrao != "AP3":
        return None
    return [x.strip() for x in re.match(r"SELECT (.*?) FROM", c.sql_reescrita, re.S).group(1).split(",")]


def do_catalogo(qid: str, plano: str | None = "real", **kw):
    xml = {"real": real, "estimado": estimado}.get(plano, lambda _: None)(qid) if plano else None
    return prescrever(sql(qid), xml, SO, colunas_cat(qid), **kw)


def derivado(densidades=None, indices=()) -> EsquemaBanco:
    """DERIVADO: instantaneo real + densidades/indices sinteticos."""
    e = EsquemaBanco.de_dict(SO.para_dict())
    for (tab, col), d in (densidades or {}).items():
        e.densidades.append(DensidadeEsq("dbo", tab, f"_WA_{col}", col, d))
    for nome, tab, chave, incl in indices:
        e.indices.append(Indice("dbo", tab, nome, "NONCLUSTERED", False, False, tuple(chave), tuple(incl)))
    return e


def ddls(p) -> list[str]:
    return [i.ddl for i in p.indices if i.decisao == "propor"]


def norm(s: str) -> str:
    return " ".join(s.split())


def reescrito(sql_in: str, esquema=SO, **kw) -> list:
    return gerar_reescritas(sql_in, esquema, ConfigPrescricao(**kw))[0]


# ================================================================================================ regra de ordem das colunas (IDX-01)
def test_v4_igualdade_antes_do_intervalo():
    p = do_catalogo("V4")
    i = p.indice_proposto("Posts")
    assert i.ddl == "CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId_Score ON dbo.Posts (OwnerUserId, Score)"
    assert [(c.coluna, c.papel) for c in i.chave] == [("OwnerUserId", "igualdade"), ("Score", "intervalo")]
    assert "igualdade(s) do filtro primeiro" in i.justificativa_ordem and "R1" in i.justificativa_ordem and "11 leituras" in i.justificativa_ordem


def test_a_ordem_nao_depende_da_ordem_em_que_as_colunas_aparecem_no_sql():
    a = prescrever("SELECT COUNT(*) FROM dbo.Posts WHERE Score > 10 AND OwnerUserId = 5", None, SO)   # SINTETICO: intervalo escrito antes
    assert ddls(a) == ["CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId_Score ON dbo.Posts (OwnerUserId, Score)"]


def test_igualdades_ordenadas_pela_seletividade_da_coluna_a_mais_seletiva_primeiro():
    """DERIVADO: densidades sinteticas. OwnerUserId (100.000 distintos) e mais seletiva que PostTypeId (8)."""
    q = "SELECT COUNT(*) FROM dbo.Posts WHERE PostTypeId = 1 AND OwnerUserId = 5 AND Score > 10"
    e = derivado({("Posts", "OwnerUserId"): 1 / 100_000, ("Posts", "PostTypeId"): 1 / 8})
    i = prescrever(q, None, e).indice_proposto("Posts")
    assert i.colunas_chave == ["OwnerUserId", "PostTypeId", "Score"] and "a mais seletiva primeiro" in i.justificativa_ordem
    invertido = derivado({("Posts", "OwnerUserId"): 1 / 8, ("Posts", "PostTypeId"): 1 / 100_000})
    assert prescrever(q, None, invertido).indice_proposto("Posts").colunas_chave == ["PostTypeId", "OwnerUserId", "Score"]


def test_sem_densidade_a_ordem_das_igualdades_e_a_do_sql_e_isso_e_dito():
    i = prescrever("SELECT COUNT(*) FROM dbo.Posts WHERE PostTypeId = 1 AND OwnerUserId = 5", None, SO).indice_proposto("Posts")
    assert i.colunas_chave == ["PostTypeId", "OwnerUserId"] and "ordem do SQL mantida" in i.justificativa_ordem


def test_junccao_depois_das_igualdades_do_filtro_e_antes_do_intervalo():
    """SINTETICO: filtro constante (PostTypeId), coluna de juncao (OwnerUserId) e intervalo (Score) na mesma tabela."""
    q = "SELECT p.Id FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId WHERE p.PostTypeId = 1 AND p.Score > 5"
    i = prescrever(q, None, SO).indice_proposto("Posts")
    assert [(c.coluna, c.papel) for c in i.chave] == [("PostTypeId", "igualdade"), ("OwnerUserId", "juncao"), ("Score", "intervalo")]
    assert "SUPOSIÇÃO" in i.justificativa_ordem


def test_no_maximo_uma_coluna_de_intervalo_na_chave_as_outras_viram_include():
    """SINTETICO: duas faixas (Score, ViewCount) e uma igualdade."""
    i = prescrever("SELECT Id FROM dbo.Posts WHERE Score > 10 AND ViewCount < 50 AND OwnerUserId = 7", None, SO).indice_proposto("Posts")
    assert i.colunas_chave == ["OwnerUserId", "Score"] and i.incluidas == ["ViewCount"]
    assert any("filtro residual" in r for r in i.ressalvas)


def test_ordem_invertida_existente_gera_proposta_com_a_ordem_do_filtro():
    """DERIVADO: indice (Score, OwnerUserId) existe; o proposto tem a ordem do filtro e a ressalva cita o antigo."""
    e = derivado(indices=[("ix_score_owner", "Posts", ["Score", "OwnerUserId"], [])])
    i = prescrever(sql("V4"), None, e).indice_proposto("Posts")
    assert i.causa == "ordem_colunas_inadequada" and i.colunas_chave == ["OwnerUserId", "Score"]
    assert any("ix_score_owner" in r for r in i.ressalvas)


def test_indice_existente_sem_problema_nao_gera_proposta():
    e = derivado(indices=[("ix_owner_score", "Posts", ["OwnerUserId", "Score"], [])])
    assert ddls(prescrever(sql("V4"), None, e)) == []


# ================================================================================================ INCLUDE
def test_include_com_as_demais_colunas_lidas_quando_a_projecao_e_estreita():
    i = do_catalogo("S1").indice_proposto("Posts")
    assert i.ddl == "CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId_cob ON dbo.Posts (OwnerUserId) INCLUDE (Score, ViewCount)"
    assert i.cobre_consulta and "projeção estreita" in i.justificativa_include
    assert prescrever(sql("F2"), real("F2"), SO).indice_proposto("Users").incluidas == ["Reputation"]      # DisplayName e a chave; Id vem do clusterizado


def test_include_nao_repete_a_chave_nem_a_chave_do_clusterizado():
    i = prescrever("SELECT Id, OwnerUserId, Score FROM dbo.Posts WHERE OwnerUserId = 5", None, SO).indice_proposto("Posts")   # SINTETICO
    assert i.incluidas == ["Score"]


def test_select_estrela_nunca_recebe_include_a_prescricao_e_reduzir_a_projecao():
    p = prescrever(sql("C1"), real("C1"), SO)                                   # SELECT * sem informar colunas
    i = p.indice_proposto("Posts")
    assert i.ddl == "CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId ON dbo.Posts (OwnerUserId)" and i.incluidas == [] and i.cobre_consulta is False
    assert "SELECT *" in i.justificativa_include and any("46.157" in r for r in i.ressalvas)       # medido na R1 (C1)
    assert p.reescritas == [] and p.reescrita_final is None                      # sem as colunas, nenhuma reescrita de SELECT *
    rec = next(r for r in p.recomendacoes if r.regra == "RW-03")
    assert "colunas_necessarias" in rec.texto and "Key Lookup" in rec.justificativa


def test_select_estrela_com_colunas_informadas_gera_include_e_reescrita_rw03():
    p = do_catalogo("C1")
    assert p.indice_proposto("Posts").ddl == "CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId_cob ON dbo.Posts (OwnerUserId) INCLUDE (Score, CreationDate)"
    r = p.reescritas[0]
    assert r.regra == "RW-03" and norm(r.sql_reescrito) == "SELECT Id, Score, CreationDate FROM dbo.Posts WHERE OwnerUserId = 22656"
    assert norm(r.sql_equivalencia) == "SELECT Id, Score, CreationDate FROM (SELECT * FROM dbo.Posts WHERE OwnerUserId = 22656) AS t"
    assert any("informadas pelo chamador" in x for x in p.indice_proposto("Posts").ressalvas)


def test_include_recusado_com_mais_colunas_que_o_limite_ou_com_tipo_max():
    """SINTETICO: 6 colunas alem da chave (limite 5) e uma coluna nvarchar(max)."""
    muitas = prescrever("SELECT Id, Score, ViewCount, AnswerCount, CommentCount, FavoriteCount, ParentId FROM dbo.Posts WHERE OwnerUserId = 5", None, SO).indice_proposto("Posts")
    assert muitas.incluidas == [] and "máximo 5" in muitas.justificativa_include and muitas.cobre_consulta is False
    largo = prescrever("SELECT Id, Body FROM dbo.Posts WHERE OwnerUserId = 5", None, SO).indice_proposto("Posts")
    assert largo.incluidas == [] and "Body é de tipo max" in largo.justificativa_include
    frouxo = replace(LIMIARES_PADRAO, include_max_colunas=6)
    assert len(prescrever("SELECT Id, Score, ViewCount, AnswerCount, CommentCount, FavoriteCount, ParentId FROM dbo.Posts WHERE OwnerUserId = 5", None, SO,
                          config=ConfigPrescricao(limiares=frouxo)).indice_proposto("Posts").incluidas) == 6


def test_sem_esquema_nao_ha_include_porque_os_tipos_sao_desconhecidos():
    i = prescrever(sql("S1"), None, None).indice_proposto("Posts")
    assert i.incluidas == [] and any("tipos desconhecidos" in x for x in i.nao_determinado) and "sem esquema" in i.justificativa_include.lower()


def test_sem_esquema_e_com_plano_nao_se_confirma_cobertura_e_o_custo_do_lookup_veta_o_indice():
    """S1 com plano real e sem esquema: 33% da tabela, cobertura nao confirmavel -> trata como nao cobrindo -> NAO indexar Posts."""
    p = prescrever(sql("S1"), real("S1"), None)
    i = next(x for x in p.indices if x.tabela == "Posts")
    assert i.decisao == "nao_propor" and "Key Lookups" in i.justificativa


# ================================================================================================ baixa seletividade
def test_c4_sem_colunas_recomenda_nao_indexar_e_manda_reduzir_a_projecao():
    p = prescrever(sql("C4"), real("C4"), SO)
    i = next(x for x in p.indices if x.tabela == "Users")
    assert i.decisao == "nao_propor" and i.ddl is None and "Não indexar" in i.justificativa and "Reduza a projeção" in i.justificativa
    assert i.leituras_depois > i.leituras_antes == 7405                      # estimativa, nao medicao
    assert ddls(p) == []


def test_c4_com_colunas_o_indice_cobrindo_e_proposto():
    assert ddls(do_catalogo("C4")) == ["CREATE NONCLUSTERED INDEX IX_Users_Location_cob ON dbo.Users (Location) INCLUDE (DisplayName, Reputation)"]


def test_regra_literal_recusa_indice_cobrindo_com_seletividade_acima_do_limiar():
    literal = ConfigPrescricao(limiares=replace(LIMIARES_PADRAO, regra_literal_seletividade=True))
    for qid, tabela in (("F4", "Posts"), ("S1", "Posts"), ("S4", "Posts")):
        p = do_catalogo(qid, config=literal)
        i = next(x for x in p.indices if x.tabela == tabela)
        assert i.decisao == "nao_propor" and "regra literal" in i.justificativa, qid
    padrao = do_catalogo("F4").indice_proposto("Posts")
    assert padrao.decisao == "propor" and any("seletividade baixa pelo limiar" in r for r in padrao.ressalvas)


def test_limiar_de_baixa_seletividade_e_configuravel():
    estrito = ConfigPrescricao(limiares=replace(LIMIARES_PADRAO, regra_literal_seletividade=True, sel_baixa=0.001))
    assert ddls(prescrever(sql("V5"), real("V5"), SO, config=estrito)) == []         # 5,7% das linhas > 0,1%
    assert len(ddls(prescrever(sql("V5"), real("V5"), SO))) == 1


# ================================================================================================ estimativa estatica de escrita e leituras
def test_toda_proposta_traz_impacto_de_escrita_rotulado_como_estimativa():
    p = do_catalogo("S1")
    for i in (x for x in p.indices if x.decisao == "propor"):
        im = i.impacto_escrita
        assert "ESTIMATIVA" in im.rotulo and (im.indices_antes, im.indices_depois) == (1, 2) and im.paginas_estimadas
        assert any("custo real de INSERT/UPDATE/DELETE" in x for x in im.nao_determinado)
    assert "[ESTIMATIVA, não medida]" in formatar(p) and "ESTIMATIVA ESTATICA" in formatar(p)


def test_ddl_gerado_e_reconhecido_pelo_catalogo_e_reversivel():
    for qid in ("F1", "S1", "V4", "C1"):
        for i in (x for x in do_catalogo(qid).indices if x.decisao == "propor"):
            assert re.match(r"CREATE NONCLUSTERED INDEX \w+ ON dbo\.\w+ \(", i.ddl)
            assert catalogo.ddl_reverter(i.ddl) == i.ddl_reverter == f"DROP INDEX {i.nome} ON dbo.{i.tabela}"
            tab, chave, incl = catalogo._assinatura_indice(i.ddl)
            assert tab == f"dbo.{i.tabela}".lower() and list(chave) == [c.lower() for c in i.colunas_chave] and incl == frozenset(c.lower() for c in i.incluidas)


def test_propostas_com_a_mesma_chave_sao_fundidas_com_a_uniao_do_include():
    """S1 tem 3 subconsultas sobre Posts com a mesma chave (OwnerUserId) e INCLUDEs diferentes: sai UM indice."""
    p = do_catalogo("S1")
    assert [i.tabela for i in p.indices if i.decisao == "propor"].count("Posts") == 1


def test_sem_plano_o_indice_sai_mas_a_regra_de_seletividade_nao_pode_ser_aplicada():
    p = prescrever(sql("V1"), None, SO)
    i = p.indice_proposto("Votes")
    assert i.decisao == "propor" and any("seletividade desconhecida" in x for x in i.nao_determinado)


# ================================================================================================ RW-01
def test_rw01_year_e_month_viram_intervalo_semiaberto_do_mes():
    c = reescrito(sql("F1"))[0]
    assert c.regra == "RW-01" and norm(c.sql_reescrito) == "SELECT COUNT(*) AS N FROM dbo.Posts WHERE CreationDate >= '20080801' AND CreationDate < '20080901'"
    assert c.trechos == ["YEAR(CreationDate) = 2008 AND MONTH(CreationDate) = 8 -> CreationDate >= '20080801' AND CreationDate < '20080901'"]


@pytest.mark.parametrize("entrada,saida", [
    ("YEAR(CreationDate) = 2010", "CreationDate >= '20100101' AND CreationDate < '20110101'"),
    ("YEAR(CreationDate) >= 2009", "CreationDate >= '20090101'"),
    ("YEAR(CreationDate) > 2009", "CreationDate >= '20100101'"),
    ("YEAR(CreationDate) < 2009", "CreationDate < '20090101'"),
    ("YEAR(CreationDate) <= 2009", "CreationDate < '20100101'"),
    ("2010 = YEAR(CreationDate)", "CreationDate >= '20100101' AND CreationDate < '20110101'"),
    ("YEAR(CreationDate) = 2008 AND MONTH(CreationDate) = 12", "CreationDate >= '20081201' AND CreationDate < '20090101'"),      # dezembro vira janeiro
    ("MONTH(CreationDate) = 2 AND YEAR(CreationDate) = 2008", "CreationDate >= '20080201' AND CreationDate < '20080301'"),
    ("DATEDIFF(day, '20100615', CreationDate) = 0", "CreationDate >= '20100615' AND CreationDate < '20100616'"),
    ("DATEDIFF(day, CreationDate, '20100615') = 0", "CreationDate >= '20100615' AND CreationDate < '20100616'"),
    ("DATEDIFF(day, '2010-06-15 13:45:00', CreationDate) = 0", "CreationDate >= '20100615' AND CreationDate < '20100616'"),    # descarta a hora do literal
    ("DATEDIFF(day, '20100615', CreationDate) = 2", "CreationDate >= '20100617' AND CreationDate < '20100618'"),
    ("DATEDIFF(day, CreationDate, '20100615') = 2", "CreationDate >= '20100613' AND CreationDate < '20100614'"),
    ("DATEDIFF(month, '20100615', CreationDate) = 1", "CreationDate >= '20100701' AND CreationDate < '20100801'"),
    ("DATEDIFF(year, CreationDate, '20100615') = 2", "CreationDate >= '20080101' AND CreationDate < '20090101'"),
    ("CONVERT(date, CreationDate) = '20100615'", "CreationDate >= '20100615' AND CreationDate < '20100616'"),
    ("CAST(CreationDate AS date) = '20100615'", "CreationDate >= '20100615' AND CreationDate < '20100616'"),
    ("CAST(CreationDate AS date) >= '20100615'", "CreationDate >= '20100615'"),
    ("CAST(CreationDate AS date) > '20100615'", "CreationDate >= '20100616'"),
    ("CAST(CreationDate AS date) <= '20100615'", "CreationDate < '20100616'"),
    ("CAST(CreationDate AS date) < '20100615'", "CreationDate < '20100615'"),
])
def test_rw01_funcao_de_data_sobre_coluna(entrada, saida):
    """SINTETICO sobre Votes.CreationDate (datetime, no esquema real)."""
    c = reescrito(f"SELECT Id FROM dbo.Votes WHERE {entrada}")
    assert len(c) == 1 and norm(c[0].sql_reescrito) == f"SELECT Id FROM dbo.Votes WHERE {saida}"


def test_rw01_mantem_os_demais_predicados_e_age_em_join_on():
    c = reescrito("SELECT p.Id FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId AND YEAR(p.CreationDate) = 2009 WHERE p.Score > 3")
    assert "p.Score > 3" in c[0].sql_reescrito and "p.CreationDate >= '20090101'" in c[0].sql_reescrito and "YEAR" not in c[0].sql_reescrito


@pytest.mark.parametrize("entrada,motivo", [
    ("MONTH(CreationDate) = 8", "MONTH(coluna) sozinho"),                       # todos os meses de todos os anos: nao e um intervalo
    ("YEAR(CreationDate) = 1500", "ano fora do intervalo"),                      # datetime comeca em 1753
    ("YEAR(CreationDate) = 2009 OR Score > 3", "dentro de OR/NOT"),
    ("DATEDIFF(week, '20100615', CreationDate) = 0", None),                      # semana depende de DATEFIRST: sem regra
    ("CONVERT(date, CreationDate, 101) = '20100615'", None),                     # estilo explicito: nao tocar
    ("CAST(CreationDate AS date) = @d", None),                                   # nao e literal
])
def test_rw01_nao_reescreve_o_que_nao_e_seguro(entrada, motivo):
    q = f"SELECT Id FROM dbo.Posts WHERE {entrada}" if "Score" in entrada or "Creation" in entrada else entrada
    cands, final, trocas, avisos, recs = gerar_reescritas(q.replace("Votes", "Posts"), SO, ConfigPrescricao())
    assert [c for c in cands if c.regra == "RW-01"] == [] and trocas == []
    if motivo:
        assert any(motivo in a for a in avisos), avisos


def test_rw01_recusa_coluna_que_nao_e_data_segundo_o_esquema():
    cands, _, _, avisos, _ = gerar_reescritas("SELECT Id FROM dbo.Users WHERE YEAR(DisplayName) = 2009", SO, ConfigPrescricao())
    assert not cands and any("nvarchar" in a and "data/hora" in a for a in avisos)


def test_rw01_sem_esquema_assume_tipo_de_data_e_declara():
    c = reescrito("SELECT Id FROM dbo.Votes WHERE YEAR(CreationDate) = 2010", None)[0]
    assert any("ASSUMIDO como data/hora" in p for p in c.premissas)


def test_rw01_upper_lower_com_colacao_insensivel_vira_igualdade():
    f2 = reescrito(sql("F2"))[0]
    assert norm(f2.sql_reescrito) == "SELECT Id, DisplayName, Reputation FROM dbo.Users WHERE DisplayName = 'JON SKEET'"
    assert any("ASSUMIDA insensível" in p for p in f2.premissas)               # o instantaneo real nao traz colacao: assume e declara
    assert norm(reescrito("SELECT Id FROM dbo.Users WHERE LOWER(Location) = N'brazil'")[0].sql_reescrito) == "SELECT Id FROM dbo.Users WHERE Location = N'brazil'"


def test_rw01_colacao_informada_pelo_esquema_vence_a_suposicao():
    ci = EsquemaBanco.de_dict(SO.para_dict()); ci.colacao_banco = "SQL_Latin1_General_CP1_CI_AS"        # DERIVADO
    cs = EsquemaBanco.de_dict(SO.para_dict()); cs.colacao_banco = "Latin1_General_CS_AS"
    assert any("informada pelo esquema" in p for p in reescrito(sql("F2"), ci)[0].premissas)
    cands, _, _, avisos, _ = gerar_reescritas(sql("F2"), cs, ConfigPrescricao())
    assert not cands and any("sensível a caixa" in a for a in avisos)
    # sem suposicao permitida e sem colacao no esquema: nao reescreve
    assert gerar_reescritas(sql("F2"), SO, ConfigPrescricao(colacao_assumida=None))[0] == []


def test_rw01_upper_com_literal_em_minusculas_nunca_e_verdadeiro_e_nao_pode_virar_igualdade():
    cands, _, _, avisos, _ = gerar_reescritas("SELECT Id FROM dbo.Users WHERE UPPER(DisplayName) = 'Jon'", SO, ConfigPrescricao())
    assert not cands and any("nunca é verdadeira" in a for a in avisos)


def test_rw01_o_tipo_pos_reescrita_define_o_papel_da_coluna_na_chave_do_indice():
    """UPPER vira igualdade (vem antes), YEAR vira intervalo (vem depois): SINTETICO em Users com DisplayName e CreationDate."""
    i = prescrever("SELECT Id FROM dbo.Users WHERE YEAR(CreationDate) = 2009 AND UPPER(DisplayName) = 'BOB'", None, SO).indice_proposto("Users")
    assert [(c.coluna, c.papel) for c in i.chave] == [("DisplayName", "igualdade"), ("CreationDate", "intervalo")]


def test_funcao_sem_reescrita_segura_nao_entra_na_chave():
    p = prescrever("SELECT Id FROM dbo.Posts WHERE LEFT(Title, 3) = 'abc'", None, SO)       # SINTETICO: LEFT nao tem regra RW-01
    assert ddls(p) == [] and any(r.regra == "RW-01" for r in p.recomendacoes)


# ================================================================================================ RW-02
def test_rw02_reproduz_s1_com_left_join_count_da_pk_e_group_by():
    c = next(x for x in prescrever(sql("S1"), real("S1"), SO).reescritas if x.regra == "RW-02")
    assert norm(c.sql_reescrito) == ("SELECT u.Id, u.DisplayName, COUNT(p.Id) AS Qtd, MAX(p.Score) AS MaxScore, SUM(p.ViewCount) AS Views FROM dbo.Users AS u "
                                     "LEFT JOIN dbo.Posts AS p ON p.OwnerUserId = u.Id WHERE u.Reputation >= 20000 GROUP BY u.Id, u.DisplayName")
    assert any("COUNT = 0" in p for p in c.premissas) and any("chave única" in p for p in c.premissas)


def test_rw02_count_estrela_vira_count_de_coluna_nao_nula_nunca_count_estrela_no_left_join():
    for q in (sql("S1"), sql("S2"), sql("S5")):
        r = next(x for x in prescrever(q, None, SO).reescritas if x.regra == "RW-02").sql_reescrito
        assert "COUNT(*)" not in r and re.search(r"COUNT\(\w+\.Id\)", r)


def test_rw02_sem_chave_primaria_no_esquema_conta_a_coluna_de_correlacao():
    """DERIVADO: o esquema sem PK de Badges nao tem coluna unica; a coluna de correlacao e nao nula nas linhas que casam (igualdade exclui NULL)."""
    e = EsquemaBanco.de_dict(SO.para_dict())
    e.indices = [i for i in e.indices if i.tabela != "Badges"]
    r = next(x for x in prescrever(sql("S5"), None, e).reescritas if x.regra == "RW-02").sql_reescrito
    assert "COUNT(b.UserId)" in r


def test_rw02_subconsulta_no_where_com_count_maior_que_constante_vira_inner_join_having():
    c = next(x for x in prescrever(sql("S4"), real("S4"), SO).reescritas if x.regra == "RW-02")
    assert norm(c.sql_reescrito) == ("SELECT u.Id, u.DisplayName FROM dbo.Users AS u JOIN dbo.Posts AS p ON p.OwnerUserId = u.Id "
                                     "WHERE u.Reputation >= 20000 GROUP BY u.Id, u.DisplayName HAVING COUNT(*) > 100")
    assert any("INNER JOIN" in p for p in c.premissas)


@pytest.mark.parametrize("q,esperado", [
    # COUNT = 0 NAO descarta o grupo vazio: precisa de LEFT JOIN e COUNT(coluna)
    ("SELECT u.Id FROM dbo.Users u WHERE (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) = 0",
     "SELECT u.Id FROM dbo.Users AS u LEFT JOIN dbo.Posts AS p ON p.OwnerUserId = u.Id GROUP BY u.Id HAVING COUNT(p.Id) = 0"),
    # MAX de grupo vazio e NULL, e NULL > 100 nunca e verdadeiro: INNER JOIN seguro
    ("SELECT u.Id FROM dbo.Users u WHERE (SELECT MAX(p.Score) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) > 100",
     "SELECT u.Id FROM dbo.Users AS u JOIN dbo.Posts AS p ON p.OwnerUserId = u.Id GROUP BY u.Id HAVING MAX(p.Score) > 100"),
    # a mesma subconsulta usada no SELECT exige LEFT JOIN para manter as linhas sem correspondentes
    ("SELECT u.Id, (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS Q FROM dbo.Users u WHERE (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) > 5",
     "SELECT u.Id, COUNT(p.Id) AS Q FROM dbo.Users AS u LEFT JOIN dbo.Posts AS p ON p.OwnerUserId = u.Id GROUP BY u.Id HAVING COUNT(p.Id) > 5"),
    # filtro adicional da subconsulta vai para o ON (nao para o WHERE), para nao descartar linhas externas
    ("SELECT u.Id, (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id AND p.Score > 10) AS Q FROM dbo.Users u",
     "SELECT u.Id, COUNT(p.Id) AS Q FROM dbo.Users AS u LEFT JOIN dbo.Posts AS p ON p.OwnerUserId = u.Id AND p.Score > 10 GROUP BY u.Id"),
    # ORDER BY: a coluna ordenada entra no GROUP BY; o alias do agregado nao
    ("SELECT u.Id, (SELECT SUM(p.ViewCount) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS V FROM dbo.Users u ORDER BY u.Reputation DESC, V",
     "SELECT u.Id, SUM(p.ViewCount) AS V FROM dbo.Users AS u LEFT JOIN dbo.Posts AS p ON p.OwnerUserId = u.Id GROUP BY u.Id, u.Reputation ORDER BY u.Reputation DESC, V"),
    # auto-juncao: o alias da subconsulta nao colide com o externo
    ("SELECT a.Id, (SELECT COUNT(*) FROM dbo.Posts b WHERE b.ParentId = a.Id) AS Respostas FROM dbo.Posts a WHERE a.PostTypeId = 1",
     "SELECT a.Id, COUNT(b.Id) AS Respostas FROM dbo.Posts AS a LEFT JOIN dbo.Posts AS b ON b.ParentId = a.Id WHERE a.PostTypeId = 1 GROUP BY a.Id"),
])
def test_rw02_sinteticos_corretos(q, esperado):
    c = [x for x in reescrito(q) if x.regra == "RW-02"]
    assert len(c) == 1 and norm(c[0].sql_reescrito) == esperado


@pytest.mark.parametrize("q,motivo", [
    ("SELECT u.Id, (SELECT AVG(p.Score) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS A FROM dbo.Users u", "COUNT, MAX, MIN ou SUM"),
    ("SELECT u.Id, (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS Q FROM dbo.Users u JOIN dbo.Badges b ON b.UserId = u.Id", "sem junções"),
    ("SELECT u.Id, (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id AND p.Score > u.Reputation) AS Q FROM dbo.Users u", "igualdade simples"),
    ("SELECT TOP 10 u.Id, (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS Q FROM dbo.Users u ORDER BY u.Id", "TOP"),
    ("SELECT u.Id, COUNT(*) AS X, (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS Q FROM dbo.Users u GROUP BY u.Id", "GROUP BY"),
    ("SELECT * FROM dbo.Users u WHERE (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) > 3", "SELECT *"),
    ("SELECT u.Id, (SELECT TOP 1 p.Score FROM dbo.Posts p WHERE p.OwnerUserId = u.Id ORDER BY p.Score DESC) AS S FROM dbo.Users u", "COUNT, MAX, MIN ou SUM"),
    ("SELECT u.Id, (SELECT COUNT(*) FROM dbo.Posts p WHERE OwnerUserId = u.Id) AS Q FROM dbo.Users u", None),         # coluna sem qualificador resolvida pelo esquema: ACEITA
])
def test_rw02_recusas_com_o_motivo(q, motivo):
    cands, _, _, avisos, recs = gerar_reescritas(q, SO, ConfigPrescricao())
    c02 = [c for c in cands if c.regra == "RW-02"]
    if motivo is None:
        assert len(c02) == 1
        return
    assert c02 == []
    assert any(motivo in r.justificativa for r in recs if r.regra == "RW-02"), (recs, avisos)


@pytest.mark.parametrize("q", [
    "SELECT u.Id, p.cnt FROM dbo.Users u CROSS APPLY (SELECT COUNT(*) AS cnt FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) p",    # APPLY: escolha deliberada
    "SELECT Id FROM dbo.Posts WHERE OwnerUserId = (SELECT MAX(u.Id) FROM dbo.Users u)",                                         # escalar, mas nao correlacionada
    "SELECT Id FROM dbo.Users u WHERE EXISTS (SELECT 1 FROM dbo.Posts p WHERE p.OwnerUserId = u.Id)",                         # semijuncao
])
def test_rw02_nao_recomenda_nada_fora_do_escopo_da_subconsulta_escalar_correlacionada(q):
    """SINTETICO."""
    p = prescrever(q, None, SO)
    assert not any(r.regra == "RW-02" for r in p.recomendacoes) and not any(c.regra == "RW-02" for c in p.reescritas)


def test_rw02_sem_esquema_recusa_porque_nao_garante_uma_linha_por_linha_externa():
    p = prescrever(sql("S1"), None, None)
    assert not any(c.regra == "RW-02" for c in p.reescritas)
    assert any("unicidade da chave" in r.justificativa for r in p.recomendacoes)


def test_rw02_nao_deixa_o_group_by_agrupar_linhas_externas_distintas():
    """DERIVADO: Users sem chave unica no esquema (nenhum PK/indice unico): a correlacao e por Id, mas nao ha como provar a unicidade."""
    e = EsquemaBanco.de_dict(SO.para_dict())
    e.indices = [i for i in e.indices if i.tabela != "Users"]
    p = prescrever(sql("S1"), None, e)
    assert not any(c.regra == "RW-02" for c in p.reescritas) and any("chave única" in r.justificativa for r in p.recomendacoes)


def test_rw02_correlacao_por_coluna_nao_unica_da_tabela_externa_e_recusada():
    """SINTETICO: correla por Users.DisplayName (nao e chave): agrupar por ela fundiria usuarios homonimos e mudaria a cardinalidade."""
    q = ("SELECT u.DisplayName, (SELECT COUNT(*) FROM dbo.Posts p WHERE p.LastEditorDisplayName = u.DisplayName) AS Q FROM dbo.Users u")
    p = prescrever(q, None, SO)
    assert not any(c.regra == "RW-02" for c in p.reescritas)
    assert any("chave única" in r.justificativa for r in p.recomendacoes if r.regra == "RW-02")


def test_rw02_dois_grupos_diferentes_reescreve_so_o_maior_e_avisa():
    q = ("SELECT u.Id, (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) AS Q, "
         "(SELECT MAX(p2.Score) FROM dbo.Posts p2 WHERE p2.OwnerUserId = u.Id) AS M, (SELECT COUNT(*) FROM dbo.Badges b WHERE b.UserId = u.Id) AS B FROM dbo.Users u")
    cands, _, _, avisos, _ = gerar_reescritas(q, SO, ConfigPrescricao())
    r = norm(next(c for c in cands if c.regra == "RW-02").sql_reescrito)
    assert "FROM dbo.Badges AS b" in r and r.count("LEFT JOIN") == 1 and "COUNT(p.Id) AS Q" in r and "MAX(p.Score) AS M" in r      # Badges ficou como subconsulta
    assert any("multiplicaria" in a for a in avisos)


# ================================================================================================ RW-03
def test_rw03_sem_colunas_so_recomendacao_em_texto():
    cands, final, _, _, recs = gerar_reescritas(sql("C2"), SO, ConfigPrescricao(), None)
    assert cands == [] and final is None and any(r.regra == "RW-03" for r in recs)


def test_rw03_colunas_inexistentes_nao_geram_reescrita():
    cands, _, _, avisos, _ = gerar_reescritas(sql("C2"), SO, ConfigPrescricao(), ["Id", "NaoExiste"])
    assert cands == [] and any("NaoExiste" in a for a in avisos)


def test_rw03_com_juncao_exige_colunas_qualificadas():
    q = "SELECT * FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId"
    assert gerar_reescritas(q, SO, ConfigPrescricao(), ["Id"])[0] == []
    c = gerar_reescritas(q, SO, ConfigPrescricao(), ["p.Id", "u.DisplayName"])[0][0]
    assert norm(c.sql_reescrito).startswith("SELECT p.Id, u.DisplayName FROM dbo.Posts AS p JOIN dbo.Users AS u")
    assert c.sql_equivalencia is None and any("junções" in a for a in c.avisos)       # nomes repetidos impedem a tabela derivada


def test_rw03_equivalencia_descarta_order_by_sem_top_e_recusa_com_top():
    ok = gerar_reescritas("SELECT * FROM dbo.Posts WHERE OwnerUserId = 5 ORDER BY Score", SO, ConfigPrescricao(), ["Id", "Score"])[0][0]
    assert "ORDER BY" not in ok.sql_equivalencia and "ORDER BY" in ok.sql_reescrito
    top = gerar_reescritas("SELECT TOP 5 * FROM dbo.Posts WHERE OwnerUserId = 5 ORDER BY Score", SO, ConfigPrescricao(), ["Id", "Score"])[0][0]
    assert top.sql_equivalencia is None and any("ORDER BY com TOP" in a for a in top.avisos)


def test_rw03_colunas_em_dicionario_por_tabela():
    p = prescrever(sql("C2"), real("C2"), SO, {"dbo.Posts": ["Id", "Score"]})
    assert norm(p.reescritas[0].sql_reescrito) == "SELECT Id, Score FROM dbo.Posts WHERE OwnerUserId = 100"


# ================================================================================================ reescrita combinada
def test_regras_diferentes_viram_candidatas_separadas_e_uma_combinada():
    q = "SELECT * FROM dbo.Posts WHERE YEAR(CreationDate) = 2009 AND OwnerUserId = 7"        # SINTETICO: AP1 + AP3
    p = prescrever(q, None, SO, ["Id", "Score"])
    assert sorted(c.regra for c in p.reescritas) == ["RW-01", "RW-03"]
    assert p.reescrita_final.regra == "COMBINADA" and p.reescrita_final.regras_aplicadas == ["RW-01", "RW-03"]
    assert norm(p.reescrita_final.sql_reescrito) == "SELECT Id, Score FROM dbo.Posts WHERE OwnerUserId = 7 AND CreationDate >= '20090101' AND CreationDate < '20100101'" or \
        norm(p.reescrita_final.sql_reescrito) == "SELECT Id, Score FROM dbo.Posts WHERE CreationDate >= '20090101' AND CreationDate < '20100101' AND OwnerUserId = 7"


# ================================================================================================ NADA e verificado sem a verificacao
def test_toda_reescrita_nasce_candidata_nao_verificada():
    for qid in [c.id for c in CAT.consultas]:
        p = do_catalogo(qid)
        for c in p.reescritas + ([p.reescrita_final] if p.reescrita_final else []):
            assert c.status == "nao_verificada" and c.verificada is False and c.verificacao is None, qid


def test_formatar_nao_mostra_reescrita_nao_verificada_como_verificada():
    texto = formatar(do_catalogo("F1"))
    assert "CANDIDATA NÃO VERIFICADA" in texto and "VERIFICADA (resultado igual" not in texto


def test_sql_nao_analisavel_nao_gera_reescrita_nem_indice():
    for texto in ("SELEC * FRM x", "", "UPDATE dbo.Posts SET Score = 1 WHERE Id = 1"):
        p = prescrever(texto, None, SO)
        assert p.reescritas == [] and p.reescrita_final is None and ddls(p) == [] and p.avisos


def test_prescrever_exige_analise_consistente_e_aceita_analise_pronta():
    from tcclab.analise_indices import analisar_indices
    a = analisar_indices(sql("V4"), real("V4"), SO)
    assert ddls(prescrever(sql("V4"), real("V4"), SO, analise=a)) == ddls(prescrever(sql("V4"), real("V4"), SO))


# ================================================================================================ verificacao (Conexao falsa)
def _falsa(hash_original: str, hash_candidata: str, linhas: int = 3):
    """O SQL original do teste tem YEAR(; a candidata nao: o roteiro responde com hashes diferentes ou iguais."""
    return ConexaoFalsa([("YEAR(", lote_hash(hash_original, linhas)), ("", lote_hash(hash_candidata, linhas))])


def test_verificar_candidatas_hash_igual_marca_equivalente_nos_dados():
    p = prescrever(sql("F1"), None, SO)
    con = _falsa("abc:1", "abc:1")
    verificar_candidatas(con, p, sql("F1"))
    c = p.reescritas[0]
    assert c.status == "equivalente_nos_dados" and c.verificada and c.verificacao["ok"] and p.reescrita_final.verificada
    assert len(con.chamadas) == 2 and con.chamadas[0] == sql("F1")                    # executou o original e a candidata (e nada mais)


def test_verificar_candidatas_hash_diferente_marca_divergente():
    p = prescrever(sql("F1"), None, SO)
    verificar_candidatas(_falsa("abc:1", "def:1"), p, sql("F1"))
    assert p.reescritas[0].status == "divergente" and not p.reescritas[0].verificada and p.reescritas[0].verificacao["ok"] is False


def test_verificar_candidatas_erro_de_sql_nunca_vira_verificada():
    p = prescrever(sql("F1"), None, SO)
    con = ConexaoFalsa([("", RuntimeError("Incorrect syntax near ..."))])
    verificar_candidatas(con, p, sql("F1"))
    assert p.reescritas[0].status == "erro" and not p.reescritas[0].verificada and "Incorrect syntax" in p.reescritas[0].verificacao["erro"]


def test_verificar_candidatas_usa_a_referencia_de_equivalencia_do_rw03():
    p = prescrever(sql("C2"), None, SO, colunas_cat("C2"))
    con = ConexaoFalsa([("", lote_hash("h:5", 5))])
    verificar_candidatas(con, p, sql("C2"))
    assert p.reescritas[0].verificada
    assert norm(con.chamadas[0]).startswith("SELECT Id, Score, CreationDate FROM (SELECT * FROM dbo.Posts WHERE OwnerUserId = 100) AS t")   # compara com a projecao, nao com o SELECT *


def test_so_a_verificacao_muda_o_status_e_ela_nao_toca_nas_outras_candidatas_nao_executadas():
    p = prescrever(sql("F1"), None, SO)
    antes = [c.status for c in p.reescritas]
    assert antes == ["nao_verificada"]
    verificar_candidatas(_falsa("x:1", "x:1"), p, sql("F1"))
    assert [c.status for c in p.reescritas] == ["equivalente_nos_dados"]
    assert "VERIFICADA (resultado igual no banco consultado)" in formatar(p)


# ================================================================================================ CLI (offline)
def test_cli_analisar_indices_e_prescrever_sao_offline(tmp_path, capsys, monkeypatch):
    from tcclab import cli
    from tcclab.config import Config

    monkeypatch.setattr(Config, "carregar", classmethod(lambda cls, **k: (_ for _ in ()).throw(AssertionError("carregou a configuracao do SQL Server"))))
    arq_sql = tmp_path / "q.sql"
    arq_sql.write_text(sql("F1"), encoding="utf-8")
    base = ["--sql", str(arq_sql), "--plano", str(FX / "pr_F1_real.sqlplan"), "--esquema", str(FX / "pr_schema_so2010.json")]
    assert cli.main(["analisar-indices", *base]) == 0
    assert "predicado_nao_sargavel" in capsys.readouterr().out
    assert cli.main(["prescrever", *base]) == 0
    saida = capsys.readouterr().out
    assert "CREATE NONCLUSTERED INDEX IX_Posts_CreationDate" in saida and "CANDIDATA NÃO VERIFICADA" in saida
    assert cli.main(["prescrever", *base, "--json"]) == 0
    import json
    assert json.loads(capsys.readouterr().out)["reescritas"][0]["status"] == "nao_verificada"


def test_cli_arquivo_inexistente_vira_mensagem_curta(tmp_path):
    from tcclab import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["prescrever", "--sql", str(tmp_path / "nao_existe.sql")])
    assert "ERRO" in str(e.value)


# ================================================================================================ robustez (SINTETICO)
ESTRANHAS = [
    "SELECT [Id], [Score] FROM [dbo].[Posts] WITH (NOLOCK) WHERE [OwnerUserId] = 5 OPTION (RECOMPILE)",
    "SELECT DISTINCT OwnerUserId FROM dbo.Posts WHERE Score > 3",
    "SELECT a.Id FROM dbo.Posts a UNION ALL SELECT b.Id FROM dbo.Posts b WHERE b.Score > 3",
    "SELECT * FROM (SELECT Id, Score FROM dbo.Posts WHERE OwnerUserId = 5) t WHERE t.Score > 3",
    "SELECT u.Id, p.cnt FROM dbo.Users u CROSS APPLY (SELECT COUNT(*) AS cnt FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) p",
    "DECLARE @x int = 5; SELECT Id FROM dbo.Posts WHERE OwnerUserId = @x",
    "SELECT Id, ROW_NUMBER() OVER (PARTITION BY OwnerUserId ORDER BY Score) rn FROM dbo.Posts WHERE PostTypeId = 1",
    "SELECT Id FROM dbo.Posts WHERE OwnerUserId IN (SELECT Id FROM dbo.Users WHERE Reputation > 1000)",
    "SELECT Id FROM dbo.Posts WHERE EXISTS (SELECT 1 FROM dbo.Votes v WHERE v.PostId = Posts.Id)",
    "SELECT Id FROM dbo.Posts WHERE Score BETWEEN 1 AND 5 AND Title IS NOT NULL",
    "SELECT TOP (10) WITH TIES Id FROM dbo.Posts ORDER BY Score DESC",
    "SELECT Id FROM dbo.Posts WHERE CreationDate >= DATEADD(day, -30, GETDATE())",
    "SELECT Id FROM dbo.Posts p WHERE p.OwnerUserId = (SELECT MAX(u.Id) FROM dbo.Users u)",
    "SELECT Id FROM nao_existe WHERE x = 1",
    "SELECT 1",
    "SELECT Id FROM dbo.Posts WHERE ISNULL(Score, 0) > 3 AND LEN(Title) > 3",
    "WITH c AS (SELECT * FROM dbo.Posts WHERE OwnerUserId = 5) SELECT * FROM c",
    "INSERT INTO dbo.Posts (Id) SELECT Id FROM dbo.Users",
    "SELECT u.Id, (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) FROM dbo.Users u",
]


@pytest.mark.parametrize("q", ESTRANHAS)
@pytest.mark.parametrize("com_esquema", [True, False])
@pytest.mark.parametrize("colunas", [None, ["Id"]])
def test_sql_de_forma_livre_nunca_levanta_excecao_e_serializa(q, com_esquema, colunas):
    p = prescrever(q, None, SO if com_esquema else None, colunas)
    formatar(p)
    p.para_json()
    assert all(c.status == "nao_verificada" for c in p.reescritas)
