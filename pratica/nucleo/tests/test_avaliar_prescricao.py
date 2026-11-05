"""Unidade: logica de comparacao e trava de seguranca de experimento/avaliar_prescricao.py. Offline.

Usa planos copiados em tests/fixtures (pr_*.sqlplan) e casos SINTETICOS; nunca conecta ao SQL Server.
"""
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from tcclab import catalogo, schema  # noqa: E402
from tcclab.prescricao import prescrever  # noqa: E402

FX = Path(__file__).parent / "fixtures"
EXP = Path(__file__).resolve().parents[2] / "experimento"
_spec = importlib.util.spec_from_file_location("avaliar_prescricao", EXP / "avaliar_prescricao.py")
av = importlib.util.module_from_spec(_spec)
sys.modules["avaliar_prescricao"] = av
_spec.loader.exec_module(av)

SO = schema.carregar(FX / "pr_schema_so2010.json")


def presc(sql_in, esquema=SO, cols=None):
    return prescrever(sql_in, None, esquema, cols)


# ------------------------------------------------------------------------------------------------ indice
def test_indice_igual_equivalente_diferente_e_sem_prescricao():
    p = presc("SELECT Id, Score FROM dbo.Posts WHERE OwnerUserId = 5")
    assert p.indice_proposto("Posts").ddl == "CREATE NONCLUSTERED INDEX IX_Posts_OwnerUserId_cob ON dbo.Posts (OwnerUserId) INCLUDE (Score)"
    c = lambda ref: av.comparar_indice(p, ref, SO)   # noqa: E731
    assert c("CREATE INDEX x ON dbo.Posts (OwnerUserId) INCLUDE (Score)")[0] == "igual"
    assert c("CREATE INDEX x ON dbo.Posts (OwnerUserId) INCLUDE (Score, Id)")[0] == "equivalente"            # Id e a chave do clusterizado: o indice ja a carrega
    classe, motivo, _, _ = c("CREATE INDEX x ON dbo.Posts (Score, OwnerUserId) INCLUDE (Score)")
    assert classe == "diferente" and "chave gerada (owneruserid) x catálogo (score, owneruserid)" in motivo
    p2 = presc("SELECT COUNT(*) FROM dbo.Posts WHERE OwnerUserId = 5 AND Score > 10")
    classe, motivo, _, _ = av.comparar_indice(p2, "CREATE INDEX x ON dbo.Posts (Score, OwnerUserId)", SO)
    assert classe == "diferente" and "mesmo conjunto de colunas, ordem diferente" in motivo
    classe, motivo, _, _ = c("CREATE INDEX x ON dbo.Posts (OwnerUserId)")
    assert classe == "diferente" and "a mais na ferramenta ['score']" in motivo
    classe, motivo, _, _ = c("CREATE INDEX x ON dbo.Posts (OwnerUserId) INCLUDE (Score, ViewCount)")
    assert classe == "diferente" and "a menos na ferramenta ['viewcount']" in motivo
    assert c("CREATE INDEX x ON dbo.Users (Reputation)")[0] == "sem prescrição"                           # nada gerado para Users


def test_indice_nao_propor_vira_diferente_com_o_motivo_da_ferramenta():
    p = prescrever("SELECT * FROM dbo.Users WHERE Location = 'United States'",
                   (FX / "pr_C4_real.sqlplan").read_text(encoding="utf-8"), SO)
    classe, motivo, ddl, _ = av.comparar_indice(p, "CREATE INDEX x ON dbo.Users (Location)", SO)
    assert classe == "diferente" and "NÃO indexar" in motivo and ddl == ""


def test_indices_adicionais_ficam_registrados():
    p = presc("SELECT p.Id FROM dbo.Posts p JOIN dbo.Users u ON u.Id = p.OwnerUserId WHERE u.Reputation > 100 AND p.PostTypeId = 1")
    _, _, _, adicionais = av.comparar_indice(p, "CREATE INDEX x ON dbo.Posts (PostTypeId, OwnerUserId)", SO)
    assert "Users (Reputation)" in adicionais


# ------------------------------------------------------------------------------------------------ reescrita
def test_reescrita_igual_apos_normalizar_caixa_espacos_ordem_do_and_e_inner_join():
    p = presc("SELECT u.Id, COUNT(*) AS N FROM dbo.Users u WHERE (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) > 100 AND u.Reputation > 5")
    ref = "select u.id, count(*) as n from dbo.users u INNER JOIN dbo.posts p on p.owneruserid = u.id where u.reputation > 5 group by u.id having count(*) > 100"
    # esta consulta tem agregacao externa: nao e reescrita; confirma o caminho 'sem prescricao' com o motivo
    assert av.comparar_reescrita(p, ref)[0] == "sem prescrição"
    q = "SELECT u.Id, u.DisplayName FROM dbo.Users u WHERE u.Reputation >= 20000 AND (SELECT COUNT(*) FROM dbo.Posts p WHERE p.OwnerUserId = u.Id) > 100"
    p2 = presc(q)
    assert av.comparar_reescrita(p2, "SELECT u.Id, u.DisplayName FROM dbo.Users u INNER JOIN dbo.Posts p ON p.OwnerUserId = u.Id "
                                     "WHERE u.Reputation >= 20000 GROUP BY u.Id, u.DisplayName HAVING COUNT(*) > 100")[0] == "igual"
    assert av.comparar_reescrita(p2, "select u.id, u.displayname from dbo.users u join dbo.posts p on p.owneruserid = u.id "
                                     "where u.reputation >= 20000 group by u.id, u.displayname having count(*) > 100")[0] == "igual"       # caixa dos identificadores


def test_reescrita_equivalente_so_por_literal_e_lados_da_igualdade_e_diferente_no_resto():
    p = presc("SELECT Id FROM dbo.Users WHERE UPPER(DisplayName) = 'JON SKEET'")
    classe, motivo, regras = av.comparar_reescrita(p, "SELECT Id FROM dbo.Users WHERE DisplayName = 'Jon Skeet'")
    assert classe == "equivalente" and "caixa de literal" in motivo and regras == "RW-01"
    assert av.comparar_reescrita(p, "SELECT Id FROM dbo.Users WHERE 'JON SKEET' = DisplayName")[0] == "equivalente"      # lados trocados
    classe, motivo, _ = av.comparar_reescrita(p, "SELECT Id FROM dbo.Users WHERE DisplayName = 'Outro'")
    assert classe == "diferente" and "gerado:" in motivo and "catálogo:" in motivo
    assert av.comparar_reescrita(p, None)[0] == "diferente"                         # a ferramenta gera e o catalogo nao tem


def test_reescrita_sem_prescricao_quando_a_ferramenta_nao_gera():
    p = presc("SELECT Id FROM dbo.Users WHERE Reputation > 5")
    assert av.comparar_reescrita(p, "SELECT Id FROM dbo.Users WHERE Reputation > 5")[0] == "sem prescrição"
    assert av.comparar_reescrita(p, None)[0] == "sem prescrição"


# ------------------------------------------------------------------------------------------------ execucao completa (sintetica) e trava
def _planos_tmp(tmp_path) -> Path:
    base = tmp_path / "planos"
    for c in catalogo.carregar(EXP / "catalogo.toml").consultas:
        (base / c.id).mkdir(parents=True)
        (base / c.id / "original.real.sqlplan").write_text((FX / f"pr_{c.id}_real.sqlplan").read_text(encoding="utf-8"), encoding="utf-8")
    return base


def test_execucao_completa_grava_csv_e_relatorio_com_a_limitacao_e_sem_verificar(tmp_path, monkeypatch):
    chamado = []
    monkeypatch.setattr("tcclab.driver.conectar", lambda *a, **k: chamado.append(a) or (_ for _ in ()).throw(AssertionError("conectou ao banco")))
    saida = tmp_path / "saida"
    rc = av.principal(["--planos", str(_planos_tmp(tmp_path)), "--saida", str(saida), "--esquema", str(FX / "pr_schema_so2010.json")])
    assert rc == 0 and not chamado
    linhas = list(csv.DictReader(open(saida / "prescricao_casos.csv", encoding="utf-8")))
    cenarios = {l["cenario"] for l in linhas}
    assert cenarios == {"real", "so_sql", "real_regra_literal", "real_sem_colunas"}       # sem planos estimados na pasta temporaria: cenario ausente, nao inventado
    assert len([l for l in linhas if l["cenario"] == "real"]) == 19 and all(l["verificacao_no_banco"] == "não executada" for l in linhas)
    md = (saida / "prescricao_relatorio.md").read_text(encoding="utf-8")
    assert "teste de regressão" in md and "não prova de generalização" in md.replace("**", "").replace("não** prova de generalização", "não prova de generalização") or "generalização" in md
    assert "NÃO EXECUTADA" in md and "--verificar-no-banco" in md and "Planos ausentes" in md and "estimado" in md.split("Planos ausentes")[1]


def test_verificar_no_banco_recusa_enquanto_ha_coleta_em_andamento(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "coleta_em_andamento", lambda: True)
    monkeypatch.setattr("tcclab.driver.conectar", lambda *a, **k: (_ for _ in ()).throw(AssertionError("conectou ao banco")))
    with pytest.raises(SystemExit) as e:
        av.principal(["--planos", str(_planos_tmp(tmp_path)), "--saida", str(tmp_path / "s"), "--verificar-no-banco"])
    assert e.value.code == 3


def test_coleta_em_andamento_assume_ocupado_se_nao_consegue_saber(monkeypatch):
    def falha(*a, **k):
        raise OSError("sem pgrep")
    monkeypatch.setattr(av.subprocess, "run", falha)
    assert av.coleta_em_andamento() is True
