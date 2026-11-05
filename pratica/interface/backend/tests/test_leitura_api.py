"""Endpoints de leitura: rodadas, catalogo, ambiente, runs, planos. Dados SINTETICOS (ver conftest)."""
import hashlib
import os
import sqlite3
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from conftest import criar_rodada_sintetica
from tcclab_api import leitura as L
from tcclab_api.main import create_app


def test_rodadas_lista_resumo_e_origem(cliente):
    r = cliente.get("/api/rodadas").json()["rodadas"]
    assert [x["rodada"] for x in r] == ["sint-1"]
    x = r[0]
    assert x["tipo"] == "coleta" and x["estado"] == "concluida"
    assert x["n_experimentos"] == 1 and x["consultas"] == ["T1"]
    assert x["n_medicoes"] == 5 * 2 * 8          # 5 blocos x 2 regimes x 8 repeticoes medidas (warmup nao conta)
    assert x["parametros_rodada"]["compat"] == 160 and x["parametros_rodada"]["maxdop"] == 1


def test_rodada_piloto_e_marcada(tmp_cfg):
    criar_rodada_sintetica(tmp_cfg.resultados, "piloto-9")
    r = TestClient(create_app(tmp_cfg)).get("/api/rodadas").json()["rodadas"]
    assert {x["rodada"]: x["tipo"] for x in r} == {"piloto-9": "piloto"}


def test_estado_em_andamento_x_interrompida(tmp_cfg):
    criar_rodada_sintetica(tmp_cfg.resultados, "viva", status="executando", mtime_antigo=False)
    criar_rodada_sintetica(tmp_cfg.resultados, "velha", status="executando", mtime_antigo=True)
    c = TestClient(create_app(tmp_cfg))
    est = {x["rodada"]: x["estado"] for x in c.get("/api/rodadas").json()["rodadas"]}
    assert est == {"viva": "em_andamento", "velha": "interrompida"}
    assert [x["rodada"] for x in c.get("/api/estado").json()["ao_vivo"]["coletas_em_andamento"]] == ["viva"]


def test_rodada_incompativel_nao_quebra_a_lista(tmp_cfg):
    pasta = criar_rodada_sintetica(tmp_cfg.resultados, "futura")
    con = sqlite3.connect(pasta / "tcclab.sqlite")
    con.execute("PRAGMA user_version = 99")
    con.commit(); con.close()
    c = TestClient(create_app(tmp_cfg))
    x = c.get("/api/rodadas").json()["rodadas"][0]
    assert x["compativel"] is False and "esquema v99" in x["motivo_incompativel"]
    assert c.get("/api/rodadas/futura/ambiente").status_code == 422


def test_rodada_inexistente_e_nome_invalido(cliente):
    assert cliente.get("/api/rodadas/naoexiste").status_code == 404
    assert cliente.get("/api/rodadas/..%2Fetc").status_code == 404
    assert cliente.get("/api/rodadas/.oculta").status_code == 404


def test_sem_pasta_de_resultados_devolve_lista_vazia(tmp_cfg):
    cfg = replace(tmp_cfg, resultados=tmp_cfg.resultados / "nao-existe")
    assert TestClient(create_app(cfg)).get("/api/rodadas").json() == {"rodadas": []}


def test_catalogo_lista_consultas_papeis_e_descricoes(cliente):
    cat = cliente.get("/api/catalogo").json()
    assert {f["arquivo"] for f in cat["fontes"]} == {"catalogo.toml", "controles_limpos.toml"}
    ids = {c["id"]: c for c in cat["consultas"]}
    assert set(ids) == {"T1", "T2", "T3", "L1"}
    assert ids["T2"]["papel"] == "controle" and ids["L1"]["papel"] == "limpa" and ids["T1"]["papel"] == "principal"
    assert cat["descricoes_antipadrao"]["AP1"].startswith("funcao sobre coluna filtrada")
    assert ids["T1"]["medida_em"][0]["rodada"] == "sint-1"
    assert ids["T3"]["medida_em"] == []          # consulta do catalogo sem medicao: lista vazia, nao inventada


def test_catalogo_ausente_devolve_503(tmp_cfg):
    cfg = replace(tmp_cfg, catalogo=tmp_cfg.catalogo.parent / "nao-existe.toml")
    r = TestClient(create_app(cfg)).get("/api/catalogo")
    assert r.status_code == 503 and r.json()["codigo"] == "catalogo_indisponivel"


def test_consulta_detalhe_variantes_planejadas_e_nativo(cliente):
    q = cliente.get("/api/consultas/T1?rodada=sint-1").json()
    nomes = [v["nome"] for v in q["variantes_planejadas"]]
    assert nomes[:4] == ["original", "reescrita", "indice", "reescrita+indice"] and "nativo" in nomes
    ind = next(v for v in q["variantes_planejadas"] if v["nome"] == "indice")
    assert ind["ddl_reverter"] == "DROP INDEX ix_t1 ON dbo.Posts"
    assert q["nativo_ddl"].startswith("CREATE INDEX ix_exp_nativo_posts")
    assert q["medida_em"][0]["experimentos"][0]["experiment_id"] == 1
    assert cliente.get("/api/consultas/ZZ9").status_code == 404
    assert cliente.get("/api/consultas/'; drop").status_code == 404


def test_ambiente_traz_snapshot_com_query_store_rotulado(cliente):
    a = cliente.get("/api/rodadas/sint-1/ambiente").json()
    amb = a["ambientes"][0]
    assert amb["compat_level"] == 160 and amb["maxdop"] == 0 and amb["emulacao_amd64_em_arm64"] is True
    assert amb["query_store"] == {"estado": "READ_WRITE", "modo_captura": "ALL", "intervalo_min": 1, "flush_s": 60, "max_storage_mb": 512}
    assert amb["experimentos"] == [1]
    assert amb["config_escopo_banco"]["MAXDOP"] == "1" and amb["maxdop_efetivo"] == 1
    assert a["parametros_rodada"]["maxdop"] == 1       # MAXDOP do escopo do banco (parametro da rodada) x instancia (0)


def test_runs_filtra_e_resume_com_run_id(cliente):
    r = cliente.get("/api/rodadas/sint-1/runs", params={"query_id": "T1", "variante": "indice", "regime": "quente"}).json()
    assert r["total"] == 1
    run = r["runs"][0]
    assert run["variante"] == "indice" and run["n"] == 8 and run["medianas"]["logical_reads"] == 100
    assert "indice" in r["filtros"]["variantes"] and r["filtros"]["regimes"] == ["frio", "quente"]


def test_plano_da_rodada_resume_operadores_e_ignora_path_do_banco(cliente):
    p = cliente.get("/api/rodadas/sint-1/planos/1").json()
    assert p["xml"].startswith("<ShowPlanXML") and p["motivo_sem_xml"] is None
    res = p["resumo"]
    assert res["valido"] and res["query_plan_hash"] == "0xE3CB399B574BC0E8" and res["eh_plano_real"]
    assert res["leituras_logicas_plano"] == 5737
    assert any("Scan" in o["operador_fisico"] for o in res["operadores"])
    assert p["runs"][0]["run_id"] >= 1
    assert cliente.get("/api/rodadas/sint-1/planos/999").status_code == 404


def test_plano_com_arquivo_ausente_e_estado_honesto(cliente, rodada):
    for f in (rodada / "tcclab_planos").glob("*.sqlplan"):
        f.unlink()
    p = cliente.get("/api/rodadas/sint-1/planos/1").json()
    assert p["xml"] is None and p["resumo"] is None and "não encontrado" in p["motivo_sem_xml"]


def test_planos_salvos_sem_pasta_e_com_pasta(tmp_cfg, rodada):
    c = TestClient(create_app(tmp_cfg))
    r = c.get("/api/consultas/T1/planos-salvos").json()
    assert r["disponivel"] is False and "salvar_planos" in r["motivo"]
    pasta = tmp_cfg.planos / "T1"
    pasta.mkdir(parents=True)
    (pasta / "original.estimado.sqlplan").write_text((rodada / "tcclab_planos" / os.listdir(rodada / "tcclab_planos")[0]).read_text())
    (pasta / "segredo.sqlplan").write_text("<x/>")
    r = c.get("/api/consultas/T1/planos-salvos").json()
    assert [p["rotulo"] for p in r["planos"]] == ["original.estimado"]
    assert c.get("/api/consultas/T1/planos-salvos/original.estimado").json()["resumo"]["valido"]
    assert c.get("/api/consultas/T1/planos-salvos/segredo").status_code == 404
    assert c.get("/api/consultas/T1/planos-salvos/..%2F..%2Foriginal.estimado").status_code == 404


def test_get_nao_altera_nenhum_sqlite(cliente, rodada):
    antes = hashlib.sha256((rodada / "tcclab.sqlite").read_bytes()).hexdigest()
    for u in ("/api/estado", "/api/rodadas", "/api/rodadas/sint-1", "/api/rodadas/sint-1/ambiente", "/api/catalogo",
              "/api/consultas/T1", "/api/rodadas/sint-1/consultas/T1/validacao", "/api/rodadas/sint-1/consultas/T1/escrita",
              "/api/rodadas/sint-1/runs", "/api/rodadas/sint-1/analise", "/api/rodadas/sint-1/planos/1",
              "/api/rodadas/sint-1/consultas/T1/repeticoes?metrica=logical_reads"):
        assert cliente.get(u).status_code == 200, u
    time.sleep(0.5)
    assert hashlib.sha256((rodada / "tcclab.sqlite").read_bytes()).hexdigest() == antes


def test_conexao_de_leitura_recusa_escrita(tmp_cfg, rodada):
    ref = L.achar_rodada(tmp_cfg, "sint-1")
    with L.abrir(ref) as con:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("DELETE FROM measurement")


def test_rodada_abortada_aparece_marcada_e_nao_bloqueia_o_ao_vivo(tmp_cfg):
    criar_rodada_sintetica(tmp_cfg.resultados, "_abortada-r1-compat160-1840", status="executando", mtime_antigo=False)
    c = TestClient(create_app(tmp_cfg))
    x = c.get("/api/rodadas").json()["rodadas"][0]
    assert x["tipo"] == "abortada" and x["estado"] == "em_andamento"       # estado vem dos dados; o tipo avisa que foi abortada
    assert c.get("/api/estado").json()["ao_vivo"]["coletas_em_andamento"] == []


def test_arquivo_sqlite_corrompido_nao_derruba_a_lista(tmp_cfg):
    pasta = tmp_cfg.resultados / "quebrada"
    pasta.mkdir()
    (pasta / "tcclab.sqlite").write_bytes(b"isto nao e um banco sqlite" * 50)
    r = TestClient(create_app(tmp_cfg)).get("/api/rodadas").json()["rodadas"][0]
    assert r["compativel"] is False and r["rodada"] == "quebrada"
