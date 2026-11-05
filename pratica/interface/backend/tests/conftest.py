"""Fixtures de teste. TODOS os dados aqui sao SINTETICOS (rotulados "SINTETICO" no titulo das consultas),
existem apenas na pasta temporaria do teste e nunca sao lidos pela interface em uso real."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import shutil
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tcclab.config import Config as ConfigNucleo
from tcclab.store import Armazem

from tcclab_api.adaptadores import AdaptadorDiagnostico, GerenteAnalise, importar_analise
from tcclab_api.config import Configuracao
from tcclab_api.main import create_app

from fake_conexao import FIXTURES, FakeConexao

COLETAR = Path(__file__).resolve().parents[3] / "experimento" / "coletar.py"

CATALOGO_TOML = '''# Catalogo SINTETICO de teste.
#   AP1 funcao sobre coluna filtrada (SINTETICO)
#   AP3 SELECT * com key lookups (SINTETICO)
[meta]
versao = 1
banco = "so_exp"
dataset = "sintetico"

[[consulta]]
id = "T1"
antipadrao = "AP1"
titulo = "SINTETICO: funcao sobre coluna"
tabelas = ["dbo.Posts"]
seletividade = "alta"
sql_original = """SELECT COUNT(*) AS N FROM dbo.Posts WHERE YEAR(CreationDate) = 2008"""
sql_reescrita = """SELECT COUNT(*) AS N FROM dbo.Posts WHERE CreationDate >= '20080101' AND CreationDate < '20090101'"""
indice = "CREATE INDEX ix_t1 ON dbo.Posts (CreationDate)"

[[consulta]]
id = "T2"
antipadrao = "AP3"
titulo = "SINTETICO: select estrela"
tabelas = ["dbo.Users"]
seletividade = "media"
papel = "controle"
sql_original = """SELECT * FROM dbo.Users WHERE Reputation > 10"""

[[consulta]]
id = "T3"
antipadrao = "AP1"
titulo = "SINTETICO: nao medida"
tabelas = ["dbo.Votes"]
seletividade = "baixa"
sql_original = """SELECT COUNT(*) AS N FROM dbo.Votes WHERE YEAR(CreationDate) = 2009"""
'''

CONTROLES_TOML = '''[meta]
versao = 1
banco = "so_exp"
dataset = "sintetico"

[[consulta]]
id = "L1"
antipadrao = "NENHUM"
titulo = "SINTETICO: limpa"
tabelas = ["dbo.Users"]
seletividade = "alta"
papel = "limpa"
sql_original = """SELECT Id FROM dbo.Users WHERE Id = 1"""
'''


def _ddl_aux() -> str:
    spec = importlib.util.spec_from_file_location("coletar_real", COLETAR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)   # so define funcoes e constantes; nao executa a coleta
    return mod.DDL_AUX


def criar_rodada_sintetica(raiz: Path, nome: str = "sint-1", *, status: str = "concluido", com_escrita: bool = True,
                           n: int = 8, segundo_experimento_descartado: bool = False, plano: bool = True,
                           mtime_antigo: bool = True, com_invalida: bool = True) -> Path:
    pasta = raiz / nome
    pasta.mkdir(parents=True, exist_ok=True)
    arm = Armazem(pasta / "tcclab.sqlite")
    db = arm.db
    db.executescript(_ddl_aux())
    rng = random.Random(7)
    db.execute(
        "INSERT INTO environment(env_id, captured_at_utc, sql_version, edition, product_version, compat_level, ce_version, cpu_count,"
        " physical_memory_mb, max_server_memory_mb, maxdop, cost_threshold, container_platform, emulacao_amd64_em_arm64,"
        " query_store_options_json, tool_version, python_version, driver, database, avisos_json, bruto_json)"
        " VALUES (1,'2026-01-01 00:00:00','SINTETICO SQL Server','Developer Edition','16.0.0.0',160,160,15,4096,4096,0,5,'Linux / Ubuntu',1,"
        " ?, '0.1.0','3.14.0','python-tds','so_exp','[]',?)",
        (json.dumps(["READ_WRITE", "ALL", 1, 60, 512]), json.dumps({"config_escopo_banco": {"MAXDOP": "1", "LEGACY_CARDINALITY_ESTIMATION": "0"}, "maxdop_efetivo": 1})),
    )
    db.execute("INSERT INTO rodada VALUES (?,?,?,?,?)", (nome, "2026-01-01 00:00:00", 160, 1, json.dumps({"n_quente": n, "n_frio": n})))

    # catalog_query + variantes e medicoes de T1
    db.execute("INSERT INTO catalog_query(query_id, antipattern_id, title, sql_original, selectivity_class) VALUES ('T1','AP1','SINTETICO: funcao sobre coluna','SELECT 1','alta')")
    plano_ids = {}
    if plano:
        for rotulo, arq in (("scan", "plano_real_scan.sqlplan"), ("seek", "plano_real_seek.sqlplan")):
            xml = (FIXTURES / arq).read_text(encoding="utf-8")
            sha = hashlib.sha256(xml.encode()).hexdigest()[:16]
            (pasta / "tcclab_planos").mkdir(exist_ok=True)
            (pasta / "tcclab_planos" / f"{sha}.sqlplan").write_text(xml, encoding="utf-8")
            qph = "0xE3CB399B574BC0E8" if rotulo == "scan" else "0x1BC93402BC66BCC1"
            cur = db.execute("INSERT INTO plan(xml_sha, kind, path, query_hash, query_plan_hash, hash_formato, statement_cost, dop, plan_logical_reads, atributos_json)"
                             " VALUES (?,?,?,?,?,?,?,?,?,?)", (sha, "real", f"/caminho/que/nao/existe/{sha}.sqlplan", "0xA3F8017680B6E2D8", qph, "fmt", 1.0, 1, 5737 if rotulo == "scan" else 3, "{}"))
            plano_ids[rotulo] = cur.lastrowid

    def experimento(exp_id: int, nome_exp: str, st: str, tentativa: int, descartado: int) -> None:
        db.execute("INSERT INTO experiment(experiment_id, name, manifest_json, env_id, seed, started_at, finished_at, status) VALUES (?,?,?,?,?,?,?,?)",
                   (exp_id, nome_exp, "{}", 1, 1, "2026-01-01 00:00:00", None if st == "executando" else "2026-01-01 00:10:00", st))
        base_var = (exp_id - 1) * 4
        variantes = [("original", "original", None), ("reescrita", "reescrita", None), ("indice", "indice", "CREATE INDEX ix_t1 ON dbo.Posts (CreationDate)"),
                     ("reescrita+indice", "prescricao", "CREATE INDEX ix_t1 ON dbo.Posts (CreationDate)")]
        lr = {"original": 1000, "reescrita": 1000, "indice": 100, "reescrita+indice": 50}
        cpu = {"original": 200, "reescrita": 190, "indice": 20, "reescrita+indice": 10}
        for i, (vn, kind, ddl) in enumerate(variantes, start=1):
            db.execute("INSERT INTO variant(variant_id, query_id, kind, nome, sql_text, ddl_apply, ddl_revert) VALUES (?,?,?,?,?,?,?)",
                       (base_var + i, "T1", kind, vn, f"SELECT {vn}", ddl, "DROP INDEX ix_t1 ON dbo.Posts" if ddl else None))
        seq = 0
        for regime in ("quente", "frio"):
            for bloco, vi in (("A1", 0), ("B", 1), ("C", 2), ("D", 3), ("A2", 0)):
                seq += 1
                vn = variantes[vi][0]
                rid = db.execute("INSERT INTO run(experiment_id, variant_id, seq, regime, block, status, started_at, finished_at, query_hash, query_plan_hash, plan_id, avisos_json)"
                                 " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                 (exp_id, base_var + vi + 1, seq, regime, bloco, "concluido", "2026-01-01 00:00:00", "2026-01-01 00:01:00",
                                  "0xA3F8017680B6E2D8", "0xE3CB399B574BC0E8" if vi < 2 else "0x1BC93402BC66BCC1",
                                  plano_ids.get("scan" if vi < 2 else "seek"), "[]")).lastrowid
                for rep in range(0, n + 1):
                    fase = "warmup" if rep == 0 else "measured"
                    c = max(0, int(cpu[vn] * rng.uniform(0.9, 1.1)))
                    db.execute("INSERT INTO measurement(run_id, rep, phase, logical_reads, physical_reads, read_ahead_reads, lob_logical_reads, scan_count, cpu_ms, elapsed_ms,"
                               " compile_cpu_ms, compile_elapsed_ms, client_ms, rows, cpu_us, elapsed_us, ts_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                               (rid, rep, fase, lr[vn], 0 if regime == "quente" else 7, 0, 0, 1, c, c + 2, 0, 0, 1.0, 1, c * 1000, c * 1000 + 50, "2026-01-01 00:00:00"))
        db.execute("INSERT INTO coleta_consulta VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                   (nome, "T1", exp_id, tentativa, "concluido", json.dumps({"aplicavel": True, "ok": True, "linhas": 5, "hash_referencia": ["x:5"], "hash_reescrita": ["x:5"]}),
                    "CREATE INDEX ix_exp_nativo_posts ON [dbo].[Posts] ([CreationDate]) INCLUDE ([Score])", json.dumps(["nota sintetica"]), 0.05, descartado, 12.0, None))

    experimento(1, f"{nome}:T1", status, 1, 1 if segundo_experimento_descartado else 0)
    if segundo_experimento_descartado:
        experimento(2, f"{nome}:T1:t2", status, 2, 0)
    if com_escrita:
        db.execute("INSERT INTO escrita_indice VALUES (?,?,?,?,?,?,?,?)", (nome, "T1", "dbo.Posts", "ix_t1", "CREATE INDEX ix_t1 ON dbo.Posts (CreationDate)", 1000, 321.5, 40))
        for op, (sem, com) in {"insert": (50, 110), "update": (60, 90), "delete": (70, 95)}.items():
            for rotulo, base in (("sem indice", sem), ("ix_t1", com)):
                for rep in range(1, 6):
                    db.execute("INSERT INTO escrita_medicao VALUES (?,?,?,?,?,?,?,?,?,?,?)", (nome, "T1", "dbo.Posts", rotulo, op, 1000, rep, base + rep % 2, base * 100, 5, 6))
    if com_invalida:   # consulta invalidada pela equivalencia: registrada na coleta, sem experimento
        db.execute("INSERT INTO coleta_consulta(rodada,query_id,tentativa,status,equivalencia_json) VALUES (?,?,0,'invalida_equivalencia',?)",
                   (nome, "T2", json.dumps({"aplicavel": True, "ok": False, "linhas": 3, "hash_referencia": ["a:3"], "hash_reescrita": ["b:3"]})))
    db.commit()
    arm.fechar()
    if mtime_antigo:
        antigo = time.time() - 3600
        import os
        os.utime(pasta / "tcclab.sqlite", (antigo, antigo))
    return pasta


@pytest.fixture()
def tmp_cfg(tmp_path) -> Configuracao:
    exp = tmp_path / "experimento"
    exp.mkdir()
    (exp / "catalogo.toml").write_text(CATALOGO_TOML, encoding="utf-8")
    (exp / "controles_limpos.toml").write_text(CONTROLES_TOML, encoding="utf-8")
    (tmp_path / "resultados").mkdir()
    return Configuracao(
        resultados=tmp_path / "resultados", catalogo=exp / "catalogo.toml", controles=exp / "controles_limpos.toml",
        planos=exp / "planos", planos_controles=exp / "planos_controles", ao_vivo_dir=tmp_path / "ao-vivo", cache_dir=tmp_path / "cache", frontend_dist=None,
        ao_vivo_habilitado=False)


@pytest.fixture()
def rodada(tmp_cfg) -> Path:
    return criar_rodada_sintetica(tmp_cfg.resultados)


@pytest.fixture()
def cliente(tmp_cfg, rodada) -> TestClient:
    return TestClient(create_app(tmp_cfg))


def cfg_ao_vivo(tmp_cfg: Configuracao) -> Configuracao:
    from dataclasses import replace
    return replace(tmp_cfg, ao_vivo_habilitado=True)


@pytest.fixture()
def cfg_nucleo_teste() -> ConfigNucleo:
    # sem ler pratica/infra/.env: nenhuma senha em teste
    return ConfigNucleo(password="x", permitidos=("so_exp*", "tcc_lab", "m0_*"))


@pytest.fixture()
def app_ao_vivo(tmp_cfg, cfg_nucleo_teste):
    """Fabrica de apps ao vivo com uma FakeConexao; devolve (cliente, conexoes_criadas)."""
    criadas: list[FakeConexao] = []

    def montar(**kw_fake):
        def fabrica(banco: str):
            c = FakeConexao(banco=banco, **kw_fake)
            criadas.append(c)
            return c
        app = create_app(cfg_ao_vivo(tmp_cfg), fabrica_conexao=fabrica, cfg_nucleo=cfg_nucleo_teste)
        return TestClient(app)

    return montar, criadas


def esperar_trabalho(cliente: TestClient, job_id: str, tempo: float = 20.0) -> dict:
    fim = time.time() + tempo
    while time.time() < fim:
        j = cliente.get(f"/api/ao-vivo/trabalhos/{job_id}").json()
        if j["estado"] != "executando":
            return j
        time.sleep(0.05)
    raise AssertionError("trabalho ao vivo não terminou")
