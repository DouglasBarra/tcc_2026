"""Unidade: Analise de indices (camada 2). Offline: SQL do catalogo + planos REAIS salvos (tests/fixtures/pr_*.sqlplan, dx_*.sqlplan;
ver pr_ORIGEM.txt e dx_ORIGEM.txt) + esquema REAL (pr_schema_so2010.json).

Esquemas "derivados" (indice ou densidade acrescentados ao instantaneo real) e SQL sinteticos sao montados aqui e rotulados.
Os casos do catalogo sao TESTE DE REGRESSAO: as regras foram escritas olhando estes mesmos casos (knowledge/26, secao 7).
"""
from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from tcclab import catalogo, schema  # noqa: E402
from tcclab.analise_indices import (  # noqa: E402
    LIMIARES_PADRAO, analisar_indices, avaliar_seletividade, estimar_impacto_escrita, formatar,
)
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


def codigos(a, tabela: str) -> list[str]:
    return [c.codigo for c in a.tabela(tabela).causas]


def derivado(**extras) -> EsquemaBanco:
    """Copia do instantaneo real com densidades e/ou indices nao clusterizados acrescentados (DERIVADO, nao e um banco real)."""
    e = EsquemaBanco.de_dict(SO.para_dict())
    for (tab, col), dens in extras.get("densidades", {}).items():
        e.densidades.append(DensidadeEsq("dbo", tab, f"_WA_{col}", col, dens))
    for nome, tab, chave, incl in extras.get("indices", []):
        e.indices.append(Indice("dbo", tab, nome, "NONCLUSTERED", False, False, tuple(chave), tuple(incl)))
    return e


# ------------------------------------------------------------------------------------------------ causas nos casos do catalogo
CAUSAS_ESPERADAS = {   # regressao: tabela -> causas na ordem
    "F1": {"Posts": ["predicado_nao_sargavel", "indice_ausente"]}, "F2": {"Users": ["predicado_nao_sargavel", "indice_ausente"]},
    "F3": {"Votes": ["predicado_nao_sargavel", "indice_ausente"]}, "F4": {"Posts": ["predicado_nao_sargavel", "indice_ausente"]},
    "F5": {"Votes": ["predicado_nao_sargavel", "indice_ausente"]},
    "S1": {"Posts": ["subconsulta_correlacionada_repetida", "indice_ausente"], "Users": ["indice_ausente"]},
    "S2": {"Votes": ["subconsulta_correlacionada_repetida", "indice_ausente"], "Posts": ["indice_ausente"]},
    "S3": {"Comments": ["subconsulta_correlacionada_repetida", "indice_ausente"], "Posts": ["indice_ausente"]},
    "S4": {"Posts": ["indice_ausente"], "Users": ["indice_ausente"]}, "S5": {"Badges": ["indice_ausente"], "Users": ["indice_ausente"]},
    "V1": {"Votes": ["indice_ausente"]}, "V2": {"Comments": ["indice_ausente"]}, "V3": {"Badges": ["indice_ausente"]},
    "V4": {"Posts": ["indice_ausente"]}, "V5": {"Users": ["indice_ausente"]},
    "C1": {"Posts": ["indice_ausente"]}, "C2": {"Posts": ["indice_ausente"]}, "C3": {"Comments": ["indice_ausente"]},
    # C4 (SELECT *, 1,2% das linhas, sem colunas informadas): o indice so na chave custaria mais que a varredura
    "C4": {"Users": ["baixa_seletividade"]},
}


@pytest.mark.parametrize("qid", sorted(CAUSAS_ESPERADAS))
def test_causas_nos_casos_do_catalogo_com_plano_real(qid):
    a = analisar_indices(sql(qid), real(qid), SO)
    assert a.sql_analisavel and a.plano == "real" and a.esquema_fornecido
    assert {t.tabela: [c.codigo for c in t.causas] for t in a.tabelas} == CAUSAS_ESPERADAS[qid]


def test_toda_causa_traz_evidencia_e_o_que_nao_foi_determinado_e_um_codigo_conhecido():
    from tcclab.analise_indices import CAUSAS
    for qid in CAUSAS_ESPERADAS:
        a = analisar_indices(sql(qid), real(qid), SO)
        for t in a.tabelas:
            for c in t.causas:
                assert c.codigo in CAUSAS and c.evidencia, (qid, t.tabela, c.codigo)


def test_f1_funcao_sobre_coluna_traz_trecho_do_sql_e_no_do_plano():
    a = analisar_indices(sql("F1"), real("F1"), SO)
    c = a.tabela("Posts").causas[0]
    assert c.codigo == "predicado_nao_sargavel" and c.recomendacao == "reescrever_predicado" and c.colunas == ("CreationDate",)
    assert any("YEAR(CreationDate) = 2008" in e for e in c.evidencia)           # trecho do SQL como escrito
    assert any("Nó 3 (Clustered Index Scan)" in e and "datepart(year" in e for e in c.evidencia)   # no do plano
    tipos = {(p.coluna, p.tipo, p.funcao) for p in a.predicados}
    assert tipos == {("CreationDate", "funcao", "YEAR"), ("CreationDate", "funcao", "MONTH")}


def test_tipos_de_predicado_do_catalogo():
    def tipos(qid):
        return sorted({(p.tabela, p.coluna, p.tipo) for p in analisar_indices(sql(qid), None, SO).predicados})
    assert tipos("V1") == [("Votes", "PostId", "igualdade")]
    assert tipos("V4") == [("Posts", "OwnerUserId", "igualdade"), ("Posts", "Score", "intervalo")]
    assert tipos("V5") == [("Users", "Reputation", "intervalo")]
    assert tipos("S2") == [("Posts", "OwnerUserId", "igualdade"), ("Votes", "PostId", "juncao")]       # correlacao = juncao, so o lado interno
    assert tipos("S4") == [("Posts", "OwnerUserId", "juncao"), ("Users", "Reputation", "intervalo")]
    assert tipos("F2") == [("Users", "DisplayName", "funcao")]


# ------------------------------------------------------------------------------------------------ seletividade do predicado
def test_seletividade_do_predicado_vem_do_plano_real_e_do_estimado_e_diz_qual():
    a = analisar_indices(sql("F1"), real("F1"), SO)
    p = a.predicados[0]
    assert p.base_sel == "real" and p.sel_real == pytest.approx(19_466 / 3_729_200) and p.sel_estimada == pytest.approx(74_602.2 / 3_729_200)
    assert p.sel_predicado == p.sel_real and p.conjunta                         # YEAR e MONTH no mesmo operador: fracao da conjuncao
    assert p.no_plano == 3 and p.cardinalidade == 3_729_200
    e = analisar_indices(sql("F1"), estimado("F1"), SO).predicados[0]
    assert e.base_sel == "estimada" and e.sel_real is None and e.sel_predicado == pytest.approx(0.02, rel=0.01)
    assert any("só estimada" in x or "sem contadores reais" in x for x in analisar_indices(sql("F1"), estimado("F1"), SO).tabela("Posts").causas[-1].nao_determinado)


def test_f4_baixa_seletividade_58_porcento_real_e_estimada():
    r = analisar_indices(sql("F4"), real("F4"), SO).predicados[0]
    e = analisar_indices(sql("F4"), estimado("F4"), SO).predicados[0]
    assert r.sel_real == pytest.approx(0.581, abs=0.001) and e.sel_estimada == pytest.approx(0.58, abs=0.001)


def test_selectivity_de_juncao_e_a_fracao_que_passa_pelo_filtro_de_bitmap():
    p = next(x for x in analisar_indices(sql("S1"), real("S1"), SO).predicados if x.tabela == "Posts")
    assert p.tipo == "juncao" and p.sel_real == pytest.approx(1_235_698 / 3_729_200)
    assert any("PROBE" in e for e in p.evidencia)
    pe = next(x for x in analisar_indices(sql("S1"), estimado("S1"), SO).predicados if x.tabela == "Posts")
    assert pe.sel_estimada == pytest.approx(0.10, abs=0.001) and pe.sel_real is None   # o estimado do bitmap e 10% da tabela


def test_predicados_no_mesmo_operador_ficam_marcados_como_conjuntos():
    a = analisar_indices(sql("V4"), real("V4"), SO)
    assert all(p.conjunta for p in a.predicados) and all(p.sel_real == pytest.approx(3316 / 3_729_200) for p in a.predicados)
    assert any("conjunção" in x for p in a.predicados for x in p.nao_determinado)


def test_sem_plano_a_seletividade_do_predicado_e_none_e_diz_por_que():
    a = analisar_indices(sql("V1"), None, SO)
    assert a.plano == "nenhum" and a.predicados[0].sel_predicado is None and a.predicados[0].base_sel is None
    assert any("sem plano" in x for x in a.predicados[0].nao_determinado) and any("plano de execução ausente" in x for x in a.nao_determinado)
    assert codigos(a, "Votes") == ["indice_ausente"]            # a causa sai do SQL e do esquema; o que falta fica registrado
    assert any("sem plano" in x for x in a.tabela("Votes").causas[0].nao_determinado)


def test_seletividade_da_coluna_so_com_densidade_do_esquema():
    assert all(p.sel_coluna is None for p in analisar_indices(sql("V4"), real("V4"), SO).predicados)
    e = derivado(densidades={("Posts", "OwnerUserId"): 1 / 100_000, ("Posts", "Score"): 1 / 2_000})
    p = {x.coluna: x for x in analisar_indices(sql("V4"), real("V4"), e).predicados}
    assert p["OwnerUserId"].sel_coluna == pytest.approx(100_000 / 3_729_195) and p["Score"].sel_coluna == pytest.approx(2_000 / 3_729_195)


def test_sem_esquema_diz_o_que_nao_sabe():
    a = analisar_indices(sql("V1"), real("V1"), None)
    assert not a.esquema_fornecido and any("esquema ausente" in x for x in a.nao_determinado)
    t = a.tabela("Votes")
    assert t.linhas is None and [i.nome for i in t.indices] == ["PK_Votes__Id"] and t.indices[0].origem == "plano" and not t.indices[0].completo
    assert any("sem esquema" in x for x in t.causas[0].nao_determinado)
    assert t.projecao_estreita is None                           # tipos desconhecidos: nao afirma


# ------------------------------------------------------------------------------------------------ indice existente
def test_indice_existente_que_nao_cobre_key_lookup_do_plano_real():
    """Plano REAL de C1 com indice so na chave (dx_c1_original_lookup): Nested Loops + Key Lookup (11.354 execucoes). Esquema = real,
    o indice aparece so no plano; a causa e `indice_nao_cobre`."""
    xml = (FX / "dx_c1_original_lookup.sqlplan").read_text(encoding="utf-8")
    a = analisar_indices(sql("C1"), xml, SO)
    c = a.tabela("Posts").causas[0]
    assert c.codigo == "indice_nao_cobre" and c.recomendacao == "cobrir_indice_ou_reduzir_projecao"
    assert c.detalhes["indice"] == "ix_exp_posts_owner" and c.detalhes["lookups"] == 11_354
    assert any("Key Lookup" in e and "11.354" in e for e in c.evidencia)
    ix = next(i for i in a.tabela("Posts").indices if i.nome == "ix_exp_posts_owner")
    assert ix.origem == "plano" and ix.tipo == "NONCLUSTERED"


def test_ordem_de_colunas_inadequada_indice_com_a_ordem_invertida():
    """DERIVADO: indice (Score, OwnerUserId) no esquema (o controle de ordem de V4) e o filtro de V4 (igualdade em OwnerUserId, intervalo em Score)."""
    e = derivado(indices=[("ix_score_owner", "Posts", ["Score", "OwnerUserId"], [])])
    a = analisar_indices(sql("V4"), None, e)
    c = a.tabela("Posts").causas[0]
    assert c.codigo == "ordem_colunas_inadequada" and c.recomendacao == "ajustar_indice" and c.colunas == ("Score", "OwnerUserId")
    assert any("prefixo utilizável: 1 de 2" in x for x in c.evidencia)


def test_ordem_correta_nao_e_acusada_e_vira_sem_problema():
    e = derivado(indices=[("ix_owner_score", "Posts", ["OwnerUserId", "Score"], [])])
    c = analisar_indices(sql("V4"), None, e).tabela("Posts").causas[0]
    assert c.codigo == "sem_problema" and any("plano ausente" in x for x in c.evidencia)   # sem plano nao confirma o uso


def test_indice_so_com_coluna_nao_inicial_nao_e_utilizavel():
    """DERIVADO: indice (Score, OwnerUserId) e filtro so por OwnerUserId = 5: a 1a coluna-chave nao esta no filtro."""
    e = derivado(indices=[("ix_score_owner", "Posts", ["Score", "OwnerUserId"], [])])
    c = analisar_indices("SELECT COUNT(*) FROM dbo.Posts WHERE OwnerUserId = 5", None, e).tabela("Posts").causas[0]
    assert c.codigo == "ordem_colunas_inadequada"


def test_indice_utilizavel_que_o_plano_ignora_fica_indeterminado_e_nao_inventa_motivo():
    """DERIVADO: indice (OwnerUserId, Score) existe, mas o plano (da linha de base, sem ele) varre a tabela; seletivo, causa nao determinada."""
    e = derivado(indices=[("ix_owner_score", "Posts", ["OwnerUserId", "Score"], [])])
    c = analisar_indices(sql("V4"), real("V4"), e).tabela("Posts").causas[0]
    assert c.codigo == "indeterminada" and c.recomendacao == "investigar"
    assert any("não pôde ser determinado" in x for x in c.nao_determinado)


# ------------------------------------------------------------------------------------------------ baixa seletividade
def test_c4_select_estrela_sem_colunas_custo_do_lookup_maior_que_a_varredura():
    a = analisar_indices(sql("C4"), real("C4"), SO)
    c = a.tabela("Users").causas[0]
    assert c.codigo == "baixa_seletividade" and c.recomendacao == "nao_indexar"
    assert c.detalhes["leituras_estimadas_com_indice"] > c.detalhes["leituras_varredura"] == 7405
    assert any("SELECT *" in e for e in c.evidencia) and not a.tabela("Users").colunas_informadas


def test_c4_com_colunas_informadas_a_projecao_e_estreita_e_o_indice_vale():
    a = analisar_indices(sql("C4"), real("C4"), SO, colunas_necessarias=["Id", "DisplayName", "Reputation"])
    t = a.tabela("Users")
    assert t.colunas_informadas and t.projecao_estreita is True and t.causas[0].codigo == "indice_ausente"


def test_colunas_informadas_inexistentes_sao_recusadas_com_aviso():
    a = analisar_indices(sql("C4"), real("C4"), SO, colunas_necessarias=["Id", "NaoExiste"])
    assert any("NaoExiste" in x for x in a.avisos) and not a.tabela("Users").colunas_informadas


def test_covering_com_seletividade_baixa_pelo_limiar_e_aceito_pelo_custo_e_recusado_pela_regra_literal():
    """F4: 58% das linhas; COUNT(*) torna o indice trivialmente cobrindo."""
    padrao = analisar_indices(sql("F4"), real("F4"), SO).tabela("Posts").causas[-1]
    assert padrao.codigo == "indice_ausente" and padrao.recomendacao == "criar_indice_cobrindo" and padrao.detalhes["ressalva_baixa_sel"]
    assert padrao.detalhes["leituras_estimadas_com_indice"] == pytest.approx(0.581 * 8331 + 3, rel=0.01)
    literal = analisar_indices(sql("F4"), real("F4"), SO, replace(LIMIARES_PADRAO, regra_literal_seletividade=True)).tabela("Posts").causas[-1]
    assert literal.codigo == "baixa_seletividade" and literal.recomendacao == "nao_indexar"


def test_limiar_configuravel_de_baixa_seletividade():
    estrito = replace(LIMIARES_PADRAO, regra_literal_seletividade=True, sel_baixa=0.001)
    # V5 devolve 5,7% das linhas de Users: baixa para o limiar de 0,1%
    assert codigos(analisar_indices(sql("V5"), real("V5"), SO, estrito), "Users") == ["baixa_seletividade"]
    assert codigos(analisar_indices(sql("V5"), real("V5"), SO), "Users") == ["indice_ausente"]


def test_avaliar_seletividade_regras():
    lim = LIMIARES_PADRAO
    assert not avaliar_seletividade(None, None, None, None, None, lim).baixa                      # desconhecida: nao decide
    assert avaliar_seletividade(0.3, 1e6, 1e5, False, None, lim).baixa                            # nao cobre: lookups ~1,2 M > varredura
    assert not avaliar_seletividade(0.0003, 3.7e6, 8e5, False, None, lim).baixa                   # nao cobre mas muito seletivo
    assert not avaliar_seletividade(0.6, 3.7e6, 8e5, True, 8331, lim).baixa                       # cobre: ~5.000 paginas < 800.000
    assert avaliar_seletividade(0.6, 3.7e6, 4000, True, 8331, lim).baixa                          # cobre mas o indice e maior que a varredura
    assert avaliar_seletividade(0.6, 3.7e6, 8e5, True, 8331, replace(lim, regra_literal_seletividade=True)).baixa   # literal: so o limiar
    assert not avaliar_seletividade(0.01, 3.7e6, 8e5, False, None, replace(lim, regra_literal_seletividade=True)).baixa


# ------------------------------------------------------------------------------------------------ outras causas e bordas
def test_subconsulta_repetida_so_com_duas_ou_mais_e_o_plano_diz_se_ja_decorrelacionou():
    s1 = analisar_indices(sql("S1"), real("S1"), SO).tabela("Posts").causas[0]
    assert s1.codigo == "subconsulta_correlacionada_repetida" and s1.detalhes["subconsultas"] == 3 and not s1.detalhes["plano_com_nested_loops"]
    assert any("já decorrelacionou" in e for e in s1.evidencia)
    s5 = analisar_indices(sql("S5"), real("S5"), SO).tabela("Badges")
    assert "subconsulta_correlacionada_repetida" not in [c.codigo for c in s5.causas] and any("S5" in n for n in s5.notas)


def test_subconsulta_com_nested_loops_correlacionado_no_plano_real():
    """Plano REAL de S1 com indice cobrindo (dx_s1_original_indice_cobrindo): o otimizador executa 3 Nested Loops correlacionados."""
    xml = (FX / "dx_s1_original_indice_cobrindo.sqlplan").read_text(encoding="utf-8")
    c = analisar_indices(sql("S1"), xml, SO).tabela("Posts").causas[0]
    assert c.codigo == "subconsulta_correlacionada_repetida" and c.detalhes["plano_com_nested_loops"]


def test_tabela_pequena_nao_justifica_indice():
    """DERIVADO: Badges com 500 linhas."""
    e = EsquemaBanco.de_dict(SO.para_dict())
    e.tabelas = [t if t.tabela != "Badges" else type(t)(t.esquema, t.tabela, 500, 4) for t in e.tabelas]
    c = analisar_indices(sql("V3"), real("V3"), e).tabela("Badges").causas[0]
    assert c.codigo == "sem_problema" and "tabela pequena" in c.evidencia[0]


def test_or_e_desigualdade_nao_sao_avaliados_como_sargaveis_e_isso_e_dito():
    a = analisar_indices("SELECT Id FROM dbo.Posts WHERE OwnerUserId = 5 OR Score > 3", None, SO)
    assert {p.tipo for p in a.predicados} == {"outro"} and any("OR/NOT" in x for x in a.nao_determinado)
    assert a.tabela("Posts").causas[0].codigo == "sem_problema"          # nenhum predicado sargavel analisado: nao inventa causa
    b = analisar_indices("SELECT Id FROM dbo.Posts WHERE Score <> 3 AND Title LIKE '%x%'", None, SO)
    assert {p.tipo for p in b.predicados} == {"outro"}


def test_like_com_prefixo_e_intervalo_e_igualdade_com_in():
    a = analisar_indices("SELECT Id FROM dbo.Posts WHERE Title LIKE 'abc%' AND OwnerUserId IN (1, 2, 3)", None, SO)
    assert {(p.coluna, p.tipo) for p in a.predicados} == {("Title", "intervalo"), ("OwnerUserId", "igualdade")}


def test_juncao_explicita_registra_os_dois_lados():
    a = analisar_indices("SELECT p.Id FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId WHERE u.Reputation > 100", None, SO)
    assert {(p.tabela, p.coluna, p.tipo, p.outra_coluna) for p in a.predicados} == {
        ("Users", "Id", "juncao", "Posts.OwnerUserId"), ("Posts", "OwnerUserId", "juncao", "Users.Id"), ("Users", "Reputation", "intervalo", None)}


def test_juncao_pela_pk_nao_mascara_um_filtro_sem_indice_na_mesma_tabela():
    """SINTETICO: Users entra na juncao pela PK (Id, atendida) e tem filtro por Reputation (sem indice): a causa e o filtro."""
    a = analisar_indices("SELECT p.Id FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId WHERE u.Reputation > 100", None, SO)
    c = a.tabela("Users").causas[0]
    assert c.codigo == "indice_ausente" and c.colunas == ("Reputation",)
    so_juncao = analisar_indices("SELECT p.Id FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId", None, SO)
    assert codigos(so_juncao, "Users") == ["sem_problema"] and codigos(so_juncao, "Posts") == ["indice_ausente"]


def test_coluna_sem_qualificador_e_resolvida_pelo_esquema_ou_declarada_nao_resolvida():
    a = analisar_indices("SELECT p.Id FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId WHERE Reputation > 100", None, SO)
    assert any(p.tabela == "Users" and p.coluna == "Reputation" for p in a.predicados)         # so Users tem Reputation
    b = analisar_indices("SELECT p.Id FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId WHERE CreationDate > '20100101'", None, SO)
    assert not any(p.coluna == "CreationDate" for p in b.predicados) and any("CreationDate" in x for x in b.nao_determinado)   # ambigua: nao escolhe
    c = analisar_indices("SELECT p.Id FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId WHERE Reputation > 100", None, None)
    assert not any(p.coluna == "Reputation" for p in c.predicados) and any("Reputation" in x for x in c.nao_determinado)       # sem esquema: nao adivinha


def test_sql_nao_analisavel_nunca_vira_sem_problema():
    for texto in ("SELEC * FRM x", "", "EXEC sp_who", "UPDATE dbo.Posts SET Score = 1 WHERE Id = 1"):
        a = analisar_indices(texto, real("V1"), SO)
        assert not a.sql_analisavel and a.motivo_sql and a.tabelas == [] and a.plano == "real"


def test_plano_invalido_e_ignorado_com_aviso_sem_excecao():
    a = analisar_indices(sql("V1"), "<ShowPlanXML", SO)
    assert a.plano == "invalido" and a.sql_analisavel and any("inválido" in x for x in a.avisos)
    assert a.predicados[0].sel_predicado is None


def test_cte_e_tabela_derivada_nao_quebram_e_nao_sao_analisadas_como_tabela_base():
    a = analisar_indices("WITH c AS (SELECT Id, OwnerUserId FROM dbo.Posts WHERE Score > 10) SELECT c.Id FROM c WHERE c.OwnerUserId = 5", None, SO)
    assert a.sql_analisavel and {t.tabela for t in a.tabelas} == {"Posts"}
    assert {(p.tabela, p.coluna) for p in a.predicados} == {("Posts", "Score")}               # o filtro sobre a CTE nao e atribuido a tabela base


def test_diagnostico_opcional_e_citado_na_evidencia():
    from tcclab.diagnostico import diagnosticar
    d = diagnosticar(sql("F1"), real("F1"))
    c = analisar_indices(sql("F1"), real("F1"), SO, diagnostico=d).tabela("Posts").causas[0]
    assert any("Diagnóstico ①" in e and "S2" in e for e in c.evidencia)


def test_formatar_mostra_predicados_causas_e_o_que_nao_foi_determinado():
    texto = formatar(analisar_indices(sql("F1"), real("F1"), SO))
    assert "predicado_nao_sargavel" in texto and "sel. coluna n/d" in texto and "Nó 3" in texto


# ------------------------------------------------------------------------------------------------ impacto de escrita (estatico)
# Tamanhos MEDIDOS pela coleta R1 (r1-compat160, tabela escrita_indice, 25/10/2026), copiados aqui como constantes de referencia.
MEDIDOS_R1 = [   # (tabela, chave, incluidas, paginas medidas)
    ("Posts", ["CreationDate"], [], 8330), ("Votes", ["CreationDate"], ["VoteTypeId"], 27638), ("Posts", ["OwnerUserId"], ["Score", "ViewCount"], 10162),
    ("Posts", ["OwnerUserId"], ["Score", "CreationDate"], 12023), ("Comments", ["UserId"], ["PostId", "Score"], 10560), ("Votes", ["PostId"], ["CreationDate"], 27624),
    ("Comments", ["PostId"], ["Score"], 8652), ("Posts", ["OwnerUserId"], [], 6470), ("Badges", ["UserId"], [], 1914), ("Votes", ["PostId"], [], 17589),
    ("Comments", ["UserId"], [], 6724), ("Posts", ["OwnerUserId", "Score"], [], 8333),
]


@pytest.mark.parametrize("tabela,chave,incl,medido", MEDIDOS_R1)
def test_tamanho_estimado_de_indice_so_com_colunas_fixas_bate_com_o_medido_na_r1(tabela, chave, incl, medido):
    im = estimar_impacto_escrita(SO, tabela, chave, incl)
    assert im.paginas_estimadas == pytest.approx(medido, rel=0.01)       # modelo ajustado a estes indices: regressao, nao validacao
    assert "ESTIMATIVA" in im.rotulo and any("modelo" in p for p in im.premissas)


def test_tamanho_com_coluna_variavel_e_so_ordem_de_grandeza_e_diz_isso():
    """F2 (DisplayName nvarchar(40)): medido 1.282 paginas na R1. Sem comprimento medio o modelo supoe metade do declarado e erra (~2x)."""
    im = estimar_impacto_escrita(SO, "Users", ["DisplayName"], ["Reputation"])
    assert 1282 < im.paginas_estimadas < 3 * 1282 and any("metade do declarado" in p for p in im.premissas)
    com_media = derivado(); com_media.densidades.append(DensidadeEsq("dbo", "Users", "st", "DisplayName", 0.0001, comprimento_medio=13.7))
    im2 = estimar_impacto_escrita(com_media, "Users", ["DisplayName"], ["Reputation"])
    assert im2.paginas_estimadas == pytest.approx(1282, rel=0.05) and not any("metade do declarado" in p for p in im2.premissas)


def test_impacto_de_escrita_conta_indices_largura_da_chave_e_colunas_atualizaveis():
    im = estimar_impacto_escrita(SO, "Posts", ["OwnerUserId", "Score"], ["ViewCount"])
    assert (im.indices_antes, im.indices_depois) == (1, 2) and im.largura_chave_bytes == 8
    assert im.largura_linha_bytes == 4 + (4 + 4 + 4 + 4) + 2                       # cabecalho + chave + incluida + Id do clusterizado + slot
    assert im.colunas_chave_atualizaveis == 3 and im.mb_estimados == pytest.approx(im.paginas_estimadas * 8 / 1024, abs=0.1)
    assert any("custo real de INSERT/UPDATE/DELETE" in x for x in im.nao_determinado)


def test_impacto_sem_esquema_ou_com_coluna_desconhecida_nao_inventa():
    sem = estimar_impacto_escrita(None, "Posts", ["OwnerUserId"])
    assert sem.paginas_estimadas is None and sem.largura_chave_bytes is None and any("esquema" in x for x in sem.nao_determinado)
    desc = estimar_impacto_escrita(SO, "Posts", ["Inexistente"])
    assert desc.paginas_estimadas is None and any("Inexistente" in x for x in desc.nao_determinado)


def test_coluna_max_no_include_conta_ponteiro_e_marca_premissa():
    im = estimar_impacto_escrita(SO, "Posts", ["OwnerUserId"], ["Body"])
    assert im.largura_linha_bytes > 4 + 4 + 24 and any("ponteiro" in p for p in im.premissas)
