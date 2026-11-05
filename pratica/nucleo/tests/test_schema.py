"""Unidade: tcclab.schema (esquema do banco). Offline: instantaneo REAL (fixtures/pr_schema_so2010.json) e Conexao falsa."""
import json
from pathlib import Path

import pytest

from conexao_falsa import ConexaoFalsa
from tcclab import schema
from tcclab.schema import DensidadeEsq, EsquemaBanco, Indice, TabelaEsq

FX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def so():
    return schema.carregar(FX / "pr_schema_so2010.json")


# ------------------------------------------------------------------------------------------------ instantaneo real
def test_carrega_o_instantaneo_real(so):
    assert (len(so.colunas), len(so.tabelas), len(so.indices)) == (61, 9, 9)
    assert so.linhas("dbo.Posts") == 3_729_195 and so.linhas("Votes") == 10_143_364
    assert so.buscar_tabela("[dbo].[Posts]").paginas == 865_691
    assert so.coluna("Posts", "ownerUserId").anulavel and not so.coluna("Posts", "Id").anulavel
    assert so.coluna("Posts", "Body").bytes == -1 and so.coluna("Users", "DisplayName").bytes == 80   # nvarchar(40), nvarchar(max)


def test_instantaneo_real_so_tem_pks_clusterizadas(so):
    assert all(i.clusterizado and i.pk and i.unico and len(i.chave) == 1 and not i.incluidas for i in so.indices)
    assert so.indice_clusterizado("posts").chave == ("Id",)
    assert so.chaves_unicas("Posts") == [("Id",)]


def test_ida_e_volta_preserva_o_formato_do_instantaneo(so, tmp_path):
    original = json.loads((FX / "pr_schema_so2010.json").read_text(encoding="utf-8"))
    destino = tmp_path / "esq.json"
    schema.salvar(so, destino)
    de_volta = schema.carregar(destino)
    assert de_volta.para_dict() == so.para_dict()
    novo = json.loads(destino.read_text(encoding="utf-8"))
    assert novo["indices"] == original["indices"] and novo["tabelas"] == original["tabelas"]   # linhas planas do instantaneo
    assert [{k: v for k, v in c.items() if k != "colacao"} for c in novo["colunas"]] == original["colunas"]


def test_sem_densidade_a_seletividade_da_coluna_e_none_nunca_inventada(so):
    assert so.densidades == []
    assert so.distintos("Posts", "OwnerUserId") is None and so.seletividade_coluna("Posts", "OwnerUserId") is None


def test_nome_ambiguo_entre_esquemas_nao_e_escolhido():
    e = EsquemaBanco(tabelas=[TabelaEsq("dbo", "T", 10, 1), TabelaEsq("arq", "T", 20, 2)])
    assert e.buscar_tabela("T") is None                      # ambiguo: nao escolhe
    assert e.buscar_tabela("arq.T").linhas == 20 and e.buscar_tabela("[dbo].[t]").linhas == 10
    assert e.buscar_tabela("nao_existe") is None


def test_agrupa_chave_e_incluidas_de_um_indice():
    d = {"colunas": [], "tabelas": [{"esquema": "dbo", "tabela": "T", "linhas": 1, "paginas": 1}],
         "indices": [{"esquema": "dbo", "tabela": "T", "indice": "ix", "tipo": "NONCLUSTERED", "unico": False, "pk": False, "ordem_chave": 2, "incluida": False, "coluna": "b"},
                     {"esquema": "dbo", "tabela": "T", "indice": "ix", "tipo": "NONCLUSTERED", "unico": False, "pk": False, "ordem_chave": 1, "incluida": False, "coluna": "a"},
                     {"esquema": "dbo", "tabela": "T", "indice": "ix", "tipo": "NONCLUSTERED", "unico": False, "pk": False, "ordem_chave": 0, "incluida": True, "coluna": "c"}]}
    e = EsquemaBanco.de_dict(d)
    assert e.indices == [Indice("dbo", "T", "ix", "NONCLUSTERED", False, False, ("a", "b"), ("c",))]   # ordem da chave pelo ordem_chave, nao pela linha


# ------------------------------------------------------------------------------------------------ densidade (sintetico)
def test_seletividade_da_coluna_e_distintos_sobre_total_quando_ha_densidade(so):
    """SINTETICO: densidade 1/1000 numa tabela de 3.729.195 linhas -> 1000 distintos."""
    e = EsquemaBanco.de_dict(so.para_dict())
    e.densidades.append(DensidadeEsq("dbo", "Posts", "_WA_Sys_x", "OwnerUserId", 1 / 1000))
    assert e.distintos("Posts", "OwnerUserId") == 1000
    assert e.seletividade_coluna("posts", "owneruserid") == pytest.approx(1000 / 3_729_195)
    assert e.seletividade_coluna("Posts", "Score") is None   # outra coluna: sem densidade


def test_densidade_sobrevive_ao_json(so, tmp_path):
    e = EsquemaBanco.de_dict(so.para_dict())
    e.densidades.append(DensidadeEsq("dbo", "Users", "st", "Location", 0.002, 299_398, "2026-10-25T10:00:00", 12, 9.5))
    schema.salvar(e, tmp_path / "e.json")
    r = schema.carregar(tmp_path / "e.json")
    assert r.densidades == e.densidades and r.distintos("Users", "Location") == 500


# ------------------------------------------------------------------------------------------------ inspecionar com Conexao falsa
def _falsa(com_estatistica=True, dbcc_falha=False):
    colunas = [("dbo", "Posts", 1, "Id", "int", 4, False, True, None), ("dbo", "Posts", 2, "OwnerUserId", "int", 4, True, False, None),
               ("dbo", "Posts", 3, "Title", "nvarchar", 500, True, False, "SQL_Latin1_General_CP1_CI_AS")]
    respostas = [
        ("DBCC SHOW_STATISTICS", RuntimeError("estatistica corrompida") if dbcc_falha else (["All density", "Average Length", "Columns"], [(0.00025, 4.0, "OwnerUserId"), (2.7e-07, 8.0, "OwnerUserId, Id")])),
        ("DATABASEPROPERTYEX", (["a", "b"], [("so_exp", "SQL_Latin1_General_CP1_CI_AS")])),
        ("sys.dm_db_stats_properties", (["x"] * 7, [("dbo", "Posts", "ix_stat", "OwnerUserId", 3_729_195, "2026-10-25T09:00:00", 17)] if com_estatistica else [])),
        ("sys.index_columns", (["x"] * 9, [("dbo", "Posts", "PK_Posts", "CLUSTERED", True, True, 1, False, "Id"),
                                           ("dbo", "Posts", "ix_owner", "NONCLUSTERED", False, False, 1, False, "OwnerUserId"),
                                           ("dbo", "Posts", "ix_owner", "NONCLUSTERED", False, False, 0, True, "Title")])),
        ("dm_db_partition_stats", (["x"] * 4, [("dbo", "Posts", 3_729_195, 865_691)])),
        ("FROM sys.columns c", (["x"] * 9, colunas)),
    ]
    return ConexaoFalsa(respostas)


def test_inspecionar_le_colunas_linhas_indices_e_densidades():
    con = _falsa()
    e = schema.inspecionar(con)
    assert e.banco == "so_exp" and e.colacao_banco == "SQL_Latin1_General_CP1_CI_AS"
    assert e.coluna("Posts", "OwnerUserId").anulavel and e.coluna("Posts", "Id").identidade
    assert e.coluna("Posts", "Title").colacao.endswith("CI_AS")
    assert e.linhas("Posts") == 3_729_195 and e.buscar_tabela("Posts").paginas == 865_691
    ix = next(i for i in e.indices_da("Posts") if i.nome == "ix_owner")
    assert ix.chave == ("OwnerUserId",) and ix.incluidas == ("Title",) and not ix.clusterizado
    assert e.chaves_unicas("Posts") == [("Id",)]
    d = e.densidade("Posts", "OwnerUserId")
    assert d.densidade == 0.00025 and d.comprimento_medio == 4.0 and d.linhas == 3_729_195 and d.modificacoes == 17
    assert e.distintos("Posts", "OwnerUserId") == 4000 and e.seletividade_coluna("Posts", "OwnerUserId") == pytest.approx(4000 / 3_729_195)


def test_inspecionar_so_executa_selects_e_dbcc_de_densidade():
    con = _falsa()
    schema.inspecionar(con)
    assert con.chamadas, "nenhuma consulta executada"
    for sql in con.chamadas:
        corpo = sql.lstrip().upper()
        assert corpo.startswith("SELECT") or corpo.startswith("DBCC SHOW_STATISTICS"), sql[:60]
        assert not any(p in corpo for p in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "CREATE ", "DROP ", "UPDATE STATISTICS"))
    dbcc = [c for c in con.chamadas if c.startswith("DBCC")]
    assert dbcc == ["DBCC SHOW_STATISTICS (N'[dbo].[Posts]', N'ix_stat') WITH DENSITY_VECTOR"]


def test_inspecionar_sem_estatistica_deixa_densidades_vazias():
    e = schema.inspecionar(_falsa(com_estatistica=False))
    assert e.densidades == [] and e.seletividade_coluna("Posts", "OwnerUserId") is None


def test_falha_numa_estatistica_vira_aviso_nao_erro():
    e = schema.inspecionar(_falsa(dbcc_falha=True))
    assert e.densidades == [] and any("nao lida" in a for a in e.avisos)


def test_inspecionar_sem_densidades_nao_chama_dbcc():
    con = _falsa()
    schema.inspecionar(con, com_densidades=False)
    assert not any("DBCC" in c or "dm_db_stats_properties" in c for c in con.chamadas)


def test_inspecionar_escapa_aspas_no_nome_da_estatistica():
    con = ConexaoFalsa([
        ("DBCC SHOW_STATISTICS", (["a", "b", "c"], [(0.5, 4.0, "x")])), ("DATABASEPROPERTYEX", (["a", "b"], [("db", "c")])),
        ("sys.dm_db_stats_properties", (["x"] * 7, [("dbo", "T'x", "st'at", "c", 10, None, 0)])),
        ("sys.index_columns", (["x"] * 9, [])), ("dm_db_partition_stats", (["x"] * 4, [("dbo", "T'x", 10, 1)])), ("FROM sys.columns c", (["x"] * 9, [])),
    ])
    schema.inspecionar(con)
    assert "N'st''at'" in next(c for c in con.chamadas if c.startswith("DBCC"))
