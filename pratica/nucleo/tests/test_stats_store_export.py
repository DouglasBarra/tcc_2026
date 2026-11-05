"""Unidade: estatistica, SQLite, exportacao e configuracao (sem SQL Server)."""
import csv
import json

import pytest

from tcclab.config import Config
from tcclab.exportar import exportar
from tcclab.runner import Variante, ordem_blocos
from tcclab.stats import resumir
from tcclab.store import Armazem


def test_resumir_sinaliza_atipico_sem_descartar():
    r = resumir([10, 10, 11, 10, 12, 10, 11, 90])
    assert r.n == 8 and r.maximo == 90
    assert r.atipicos == [7]  # sinalizado
    assert r.mediana == 10.5


def test_resumir_constante():
    r = resumir([5737.0] * 10)
    assert r.dp == 0 and r.cv == 0 and r.atipicos == []


def test_ordem_aba_e_semente():
    v = [Variante("antes", "x"), Variante("depois", "x")]
    assert ordem_blocos(v, "ABA", 1) == [("A1", 0), ("B", 1), ("A2", 0)]
    a = ordem_blocos(v + [Variante("c", "x")], "aleatoria", 7)
    b = ordem_blocos(v + [Variante("c", "x")], "aleatoria", 7)
    assert a == b  # mesma semente, mesma ordem
    assert sorted(i for _, i in a) == [0, 1, 2]


def test_lista_de_permissao():
    cfg = Config(permitidos=("tcc_lab", "m0_*"))
    assert cfg.banco_permitido("tcc_lab") and cfg.banco_permitido("M0_teste")
    assert not cfg.banco_permitido("StackOverflow2010") and not cfg.banco_permitido("master")
    with pytest.raises(PermissionError):
        cfg.exigir_banco_permitido("StackOverflow2010")
    assert "segredo" not in repr(Config(password="segredo"))  # a senha nunca aparece em logs


def test_esquema_sqlite_e_exportacao(tmp_path):
    arm = Armazem(tmp_path / "t.sqlite")
    tabelas = {r["name"] for r in arm.consultar("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"environment", "catalog_query", "variant", "experiment", "run", "measurement", "measurement_io",
            "qs_sample", "dmv_sample", "plan"} <= tabelas
    amb = dict(captured_at_utc="2026-10-04 00:00:00", sql_version="v", edition="e", product_version="p", compat_level=160,
               ce_version=160, cpu_count=1, physical_memory_mb=1, max_server_memory_mb=1, maxdop=0, cost_threshold=5,
               container_platform="Linux", emulacao_amd64_em_arm64=True, query_store_options=["READ_WRITE"],
               tool_version="0", tool_commit=None, python_version="3.14", python_lock_hash=None, driver="d",
               database="tcc_lab", avisos=[])
    env = arm.novo_ambiente(amb)
    exp = arm.novo_experimento("e", {"x": 1}, env, 42, "2026-10-04 00:00:00")
    arm.garantir_consulta("Q", "t", "SELECT 1")
    var = arm.nova_variante("Q", "antes", "original", "SELECT 1", None, None)
    run = arm.novo_run(exp, var, 1, "quente", "A1", "2026-10-04 00:00:00")

    class M:  # medicao minima
        logical_reads, physical_reads, read_ahead_reads, lob_logical_reads, scan_count = 5737, 0, 0, 0, 1
        cpu_ms, elapsed_ms, compile_cpu_ms, compile_elapsed_ms, client_ms, rows = 13, 13, 0, 0, 14.2, 1
        result_hash, io_raw, time_raw, ts_utc = "h", "io", "t", "2026-10-04 00:00:01"
        cpu_us, elapsed_us, dmv_logical_reads = 13400, 13410, 5737
        io_por_tabela = [dict(tabela="m0_sanidade", scan_count=1, logical_reads=5737, physical_reads=0, read_ahead_reads=0, lob_logical_reads=0)]

    arm.gravar_medicao(run, 1, "measured", M())
    arm.fechar_experimento(exp, "concluido", "2026-10-04 00:01:00", [{"seq": 1}])
    arquivos = exportar(arm, exp, tmp_path / "out", "csv")
    nomes = {a.name for a in arquivos}
    assert {"measurements.csv", "environment.json", "manifest.json", "runs.csv"} <= nomes
    linhas = list(csv.DictReader((tmp_path / "out" / "measurements.csv").open(encoding="utf-8")))
    assert len(linhas) == 1 and linhas[0]["logical_reads"] == "5737" and linhas[0]["semente"] == "42"
    exportar(arm, exp, tmp_path / "outj", "json")
    assert json.loads((tmp_path / "outj" / "dados.json").read_text())["measurements"][0]["variante"] == "antes"
    with pytest.raises(KeyError):
        exportar(arm, 999, tmp_path / "x")
    arm.fechar()
