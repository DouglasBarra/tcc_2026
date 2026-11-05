"""Caminhos ao vivo SOMENTE com Conexao falsa (fake_conexao.py). Nenhum teste toca em SQL Server.

Cobre: desligado por padrao (503), trava global (409), coleta externa em andamento (409), entradas perigosas (422),
banco fora da lista (403), diagnostico via plano estimado, validacao antes x depois com gravacao em SQLite proprio.
"""
import hashlib
import threading
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import tcclab_api.adaptadores as AD
from conftest import cfg_ao_vivo, criar_rodada_sintetica, esperar_trabalho
from fake_conexao import FakeConexao, LEITURAS_COM_INDICE, LEITURAS_SEM_INDICE
from tcclab_api.ao_vivo import validar_consulta, validar_ddl_indice, EntradaInvalida
from tcclab_api.main import create_app

DIAG = {"sql": "SELECT COUNT(*) AS N FROM dbo.Posts WHERE YEAR(CreationDate) = 2008"}
VALID = {"sql_antes": "SELECT COUNT(*) AS N FROM dbo.Posts WHERE YEAR(CreationDate) = 2008",
         "sql_depois": "SELECT COUNT(*) AS N FROM dbo.Posts WHERE CreationDate >= '20080101' AND CreationDate < '20090101'",
         "ddl_indice": "CREATE INDEX ix_t1 ON dbo.Posts (CreationDate)", "query_id": "T1",
         "regimes": ["quente"], "repeticoes": {"quente": 4, "frio": 2}, "aquecimentos": 1}


# ------------------------------------------------------------------ desligado por padrao
def test_ao_vivo_desligado_por_padrao_devolve_503_e_nao_cria_conexao(cliente):
    e = cliente.get("/api/estado").json()["ao_vivo"]
    assert e["habilitado"] is False and "TCCLAB_AO_VIVO=1" in e["motivo_desabilitado"]
    r = cliente.post("/api/ao-vivo/diagnosticar", json=DIAG)
    assert r.status_code == 503 and r.json()["codigo"] == "ao_vivo_desabilitado"
    r = cliente.post("/api/ao-vivo/validar", json=VALID)
    assert r.status_code == 503


def test_fabrica_padrao_so_existe_quando_habilitado(tmp_cfg):
    desligado = create_app(tmp_cfg)
    assert desligado.state.ao_vivo.fabrica is None                       # nenhum caminho para o SQL Server
    ligado = create_app(cfg_ao_vivo(tmp_cfg), cfg_nucleo=None)
    assert callable(ligado.state.ao_vivo.fabrica)                         # existe, mas so conecta quando chamada (nao chamada aqui)


# ------------------------------------------------------------------ validacao de entrada
@pytest.mark.parametrize("sql", [
    "", "   ", "DELETE FROM dbo.Posts", "SELECT 1; DROP TABLE dbo.Posts", "SELECT * INTO dbo.copia FROM dbo.Posts",
    "EXEC sp_who", "SELECT 1; SELECT 2", "UPDATE dbo.Posts SET Score = 0", "WITH x AS (SELECT 1) DELETE FROM dbo.Posts",
    "SELECT * FROM OPENROWSET('SQLNCLI','x','y')", "/* ok */ DBCC FREEPROCCACHE", "SELECT 1 /*c*/; TRUNCATE TABLE t",
    "x" * 20_001,
])
def test_validar_consulta_recusa(sql):
    with pytest.raises(EntradaInvalida):
        validar_consulta(sql)


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM dbo.Posts WHERE Title = 'DROP TABLE; DELETE'",
    "-- comentario\nSELECT Id FROM dbo.Users WHERE LastUpdate IS NULL",
    "WITH c AS (SELECT Id FROM dbo.Users) SELECT * FROM c;",
    "SELECT CreationDate, UPDATEDAT = 1 FROM dbo.Posts",
])
def test_validar_consulta_aceita_select_legitimo(sql):
    assert validar_consulta(sql) == sql


@pytest.mark.parametrize("ddl", ["DROP INDEX ix ON t", "CREATE INDEX ix ON t (a); DROP TABLE t", "CREATE TABLE t (a int)",
                                 "CREATE INDEX ix ON t (a) WHERE a > 1"])
def test_ddl_recusado(ddl):
    with pytest.raises(EntradaInvalida):
        validar_ddl_indice(ddl)


def test_ddl_aceito():
    assert validar_ddl_indice("CREATE INDEX ix_a ON dbo.Posts ([OwnerUserId], Score) INCLUDE (ViewCount);")


# ------------------------------------------------------------------ diagnostico
def test_diagnosticar_devolve_plano_estimado_e_diagnostico_indisponivel(app_ao_vivo):
    montar, criadas = app_ao_vivo
    c = montar()
    r = c.post("/api/ao-vivo/diagnosticar", json=DIAG)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["origem"] == "ao_vivo" and d["tipo_plano"] == "estimado" and d["banco"] == "so_exp"
    assert d["plano"]["valido"] and d["plano"]["eh_plano_real"] is False and d["plano"]["operadores"]
    if AD.importar_diagnostico()[0] is None:
        assert d["diagnostico"] == {"disponivel": False, "motivo": "módulo de diagnóstico ainda não disponível"}
    assert len(criadas) == 1 and criadas[0].fechada                         # conexao sempre fechada
    assert not any("EXEC" in s.upper() for s in criadas[0].chamadas)
    assert criadas[0].showplan is False                                     # SHOWPLAN_XML desligado ao final
    assert c.get("/api/estado").json()["ao_vivo"]["trava"]["ocupada"] is False   # trava liberada


def test_diagnosticar_usa_o_modulo_de_diagnostico_quando_existe(tmp_cfg, cfg_nucleo_teste):
    class Mod:
        @staticmethod
        def diagnosticar(sql, plano_xml=None):
            return {"achados": [{"regra": "S1", "plano_visto": bool(plano_xml)}]}
    diag = AD.AdaptadorDiagnostico(Mod, None)
    app = create_app(cfg_ao_vivo(tmp_cfg), fabrica_conexao=lambda b: FakeConexao(banco=b), cfg_nucleo=cfg_nucleo_teste, diagnostico=diag)
    d = TestClient(app).post("/api/ao-vivo/diagnosticar", json=DIAG).json()
    assert d["diagnostico"] == {"disponivel": True, "erro": None, "resultado": {"achados": [{"regra": "S1", "plano_visto": True}]}}
    assert TestClient(app).get("/api/estado").json()["modulos"]["diagnostico"] == {"disponivel": True, "motivo": None}


def test_modulo_de_diagnostico_com_falha_vira_estado(tmp_cfg, cfg_nucleo_teste):
    class Mod:
        @staticmethod
        def diagnosticar(sql, plano_xml=None):
            raise ValueError("regra quebrou")
    app = create_app(cfg_ao_vivo(tmp_cfg), fabrica_conexao=lambda b: FakeConexao(banco=b), cfg_nucleo=cfg_nucleo_teste,
                     diagnostico=AD.AdaptadorDiagnostico(Mod, None))
    d = TestClient(app).post("/api/ao-vivo/diagnosticar", json=DIAG).json()["diagnostico"]
    assert d["disponivel"] is True and "regra quebrou" in d["erro"] and d["resultado"] is None


def test_diagnosticar_recusa_sql_perigoso_e_banco_fora_da_lista(app_ao_vivo):
    montar, criadas = app_ao_vivo
    c = montar()
    r = c.post("/api/ao-vivo/diagnosticar", json={"sql": "DROP TABLE dbo.Posts"})
    assert r.status_code == 422 and r.json()["codigo"] == "entrada_invalida"
    r = c.post("/api/ao-vivo/diagnosticar", json=DIAG | {"banco": "StackOverflow2010"})
    assert r.status_code == 403 and r.json()["codigo"] == "banco_nao_permitido"
    r = c.post("/api/ao-vivo/diagnosticar", json=DIAG | {"banco": "master; DROP"})
    assert r.status_code == 422
    assert criadas == []         # nenhuma conexao criada para pedidos recusados


# ------------------------------------------------------------------ validacao antes x depois
def test_validar_executa_antes_x_depois_e_grava_em_sqlite_proprio(app_ao_vivo, tmp_cfg, rodada):
    antes_coleta = hashlib.sha256((rodada / "tcclab.sqlite").read_bytes()).hexdigest()
    montar, criadas = app_ao_vivo
    c = montar()
    r = c.post("/api/ao-vivo/validar", json=VALID)
    assert r.status_code == 202, r.text
    job = esperar_trabalho(c, r.json()["id"])
    assert job["estado"] == "concluido", job
    assert job["rodada"].startswith("ao-vivo-") and job["experiment_id"] == 1
    # a rodada ao vivo aparece como rodada de origem 'ao_vivo' e a Validacao le o que o runner gravou
    rodadas = {x["rodada"]: x for x in c.get("/api/rodadas").json()["rodadas"]}
    assert rodadas[job["rodada"]]["tipo"] == "ao_vivo" and rodadas[job["rodada"]]["estado"] == "concluida"
    v = c.get(f"/api/rodadas/{job['rodada']}/consultas/T1/validacao").json()
    assert v["disponivel"]
    lr = {g["variante"]: g["mediana"] for g in v["grupos"] if g["metrica"] == "logical_reads" and g["regime"] == "quente"}
    assert lr == {"antes": LEITURAS_SEM_INDICE, "depois": LEITURAS_COM_INDICE}      # valores FABRICADOS pela conexao falsa
    assert v["tratamento_padrao"] == "depois"
    # conexao fechada, indice revertido, trava liberada e rodada de coleta intacta
    assert criadas[0].fechada and criadas[0].indice_aplicado is False
    assert c.get("/api/estado").json()["ao_vivo"]["trava"]["ocupada"] is False
    assert hashlib.sha256((rodada / "tcclab.sqlite").read_bytes()).hexdigest() == antes_coleta
    assert (tmp_cfg.ao_vivo_dir / job["id"] / "tcclab.sqlite").is_file()


def test_validar_equivalencia_divergente_invalida_e_libera_trava(app_ao_vivo):
    montar, criadas = app_ao_vivo
    c = montar(divergir_se="20090101")
    j = c.post("/api/ao-vivo/validar", json=VALID).json()
    j = esperar_trabalho(c, j["id"])
    assert j["estado"] == "invalida_equivalencia" and j["experiment_id"] is None
    assert not any(s.startswith("CREATE INDEX") for s in criadas[0].chamadas)      # nem chegou a criar indice
    assert c.get("/api/estado").json()["ao_vivo"]["trava"]["ocupada"] is False


def test_validar_erro_do_servidor_libera_trava_e_reverte(app_ao_vivo):
    montar, criadas = app_ao_vivo
    c = montar(falhar_se="UPDATE STATISTICS")
    j = esperar_trabalho(c, c.post("/api/ao-vivo/validar", json=VALID).json()["id"])
    assert j["estado"] == "erro" and "falha simulada" in j["erro"]
    assert criadas[0].fechada
    assert c.get("/api/estado").json()["ao_vivo"]["trava"]["ocupada"] is False


def test_validar_limites_e_entradas(app_ao_vivo):
    montar, criadas = app_ao_vivo
    c = montar()
    assert c.post("/api/ao-vivo/validar", json=VALID | {"repeticoes": {"quente": 9999}}).status_code == 422
    assert c.post("/api/ao-vivo/validar", json=VALID | {"aquecimentos": 99}).status_code == 422
    assert c.post("/api/ao-vivo/validar", json=VALID | {"regimes": ["tepido"]}).status_code == 422
    assert c.post("/api/ao-vivo/validar", json=VALID | {"sql_depois": None, "ddl_indice": None}).status_code == 422
    assert c.post("/api/ao-vivo/validar", json=VALID | {"ddl_indice": "DROP TABLE t"}).status_code == 422
    assert c.post("/api/ao-vivo/validar", json=VALID | {"query_id": "NAOEXISTE"}).status_code == 422
    assert c.post("/api/ao-vivo/validar", json=VALID | {"tabelas": ["a;drop"]}).status_code == 422
    assert c.post("/api/ao-vivo/validar", json=VALID | {"banco": "master"}).status_code == 403
    assert criadas == []


# ------------------------------------------------------------------ TRAVA (409)
def test_trava_global_devolve_409_enquanto_outro_experimento_roda(app_ao_vivo):
    montar, criadas = app_ao_vivo
    segurar, iniciou = threading.Event(), threading.Event()
    c = montar(bloquear=segurar, iniciou=iniciou)
    try:
        primeiro = c.post("/api/ao-vivo/validar", json=VALID)
        assert primeiro.status_code == 202
        assert iniciou.wait(10)
        # com o primeiro em andamento: validar, diagnosticar e ate o estado mostram a trava
        r = c.post("/api/ao-vivo/validar", json=VALID)
        assert r.status_code == 409 and r.json()["codigo"] == "trava_ocupada" and "validação ao vivo" in r.json()["dono"]
        r = c.post("/api/ao-vivo/diagnosticar", json=DIAG)
        assert r.status_code == 409 and r.json()["codigo"] == "trava_ocupada"
        t = c.get("/api/estado").json()["ao_vivo"]["trava"]
        assert t["ocupada"] is True and "validação ao vivo" in t["dono"]
        assert len(criadas) == 1                    # os pedidos recusados nao abriram conexao
    finally:
        segurar.set()
    j = esperar_trabalho(c, primeiro.json()["id"])
    assert j["estado"] == "concluido"
    assert c.post("/api/ao-vivo/diagnosticar", json=DIAG).status_code == 200   # liberada: volta a aceitar


def test_coleta_externa_em_andamento_devolve_409(tmp_cfg, cfg_nucleo_teste):
    criar_rodada_sintetica(tmp_cfg.resultados, "coleta-viva", status="executando", mtime_antigo=False)
    criadas = []
    app = create_app(cfg_ao_vivo(tmp_cfg), fabrica_conexao=lambda b: criadas.append(b) or FakeConexao(banco=b), cfg_nucleo=cfg_nucleo_teste)
    c = TestClient(app)
    for rota, corpo in (("/api/ao-vivo/diagnosticar", DIAG), ("/api/ao-vivo/validar", VALID)):
        r = c.post(rota, json=corpo)
        assert r.status_code == 409 and r.json()["codigo"] == "coleta_em_andamento"
        assert r.json()["coletas"][0]["rodada"] == "coleta-viva"
    assert criadas == []


def test_coleta_antiga_interrompida_nao_bloqueia(tmp_cfg, cfg_nucleo_teste):
    criar_rodada_sintetica(tmp_cfg.resultados, "coleta-parada", status="executando", mtime_antigo=True)
    app = create_app(cfg_ao_vivo(tmp_cfg), fabrica_conexao=lambda b: FakeConexao(banco=b), cfg_nucleo=cfg_nucleo_teste)
    assert TestClient(app).post("/api/ao-vivo/diagnosticar", json=DIAG).status_code == 200


def test_trabalhos_listagem_e_404(app_ao_vivo):
    montar, _ = app_ao_vivo
    c = montar()
    j = esperar_trabalho(c, c.post("/api/ao-vivo/validar", json=VALID).json()["id"])
    assert [t["id"] for t in c.get("/api/ao-vivo/trabalhos").json()["trabalhos"]] == [j["id"]]
    assert c.get("/api/ao-vivo/trabalhos/inexistente").status_code == 404


def test_diagnostico_offline_do_catalogo_usa_tcclab_diagnostico_real(tmp_cfg, rodada):
    """Usa o modulo REAL do nucleo (sem Conexao, sem SQL Server) sobre o plano salvo."""
    mod, motivo = AD.importar_diagnostico()
    if mod is None:
        pytest.skip(motivo)
    pasta = tmp_cfg.planos / "T1"
    pasta.mkdir(parents=True)
    xml = next((rodada / "tcclab_planos").glob("*.sqlplan")).read_text()
    (pasta / "original.real.sqlplan").write_text(xml)
    c = TestClient(create_app(tmp_cfg))
    r = c.post("/api/consultas/T1/diagnostico", json={"rotulo_plano": "original.real"}).json()
    d = r["diagnostico"]
    assert d["disponivel"] is True and d["erro"] is None
    res = d["resultado"]
    assert res["plano"] == "real" and {"achados", "regras_executadas", "regras_ignoradas", "sql_analisavel"} <= set(res)
    assert any(a["regra"] == "S2" for a in res["achados"])        # YEAR(CreationDate) = 2008 em T1 (SQL do catalogo sintetico)
    sem_plano = c.post("/api/consultas/T1/diagnostico", json={}).json()["diagnostico"]["resultado"]
    assert sem_plano["plano"] == "nenhum" and "P1" in sem_plano["regras_ignoradas"]
    assert c.post("/api/consultas/T1/diagnostico", json={"rotulo_plano": "original.real; x"}).status_code == 404
    assert c.post("/api/consultas/ZZ9/diagnostico", json={}).status_code == 404


def test_planos_de_controles_limpos_sao_encontrados(tmp_cfg, rodada):
    pasta = tmp_cfg.planos_controles / "L1"
    pasta.mkdir(parents=True)
    (pasta / "original.estimado.sqlplan").write_text(next((rodada / "tcclab_planos").glob("*.sqlplan")).read_text())
    r = TestClient(create_app(tmp_cfg)).get("/api/consultas/L1/planos-salvos").json()
    assert [p["rotulo"] for p in r["planos"]] == ["original.estimado"]
