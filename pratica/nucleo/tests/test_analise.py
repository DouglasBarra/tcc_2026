"""Unidade: analise estatistica e relatorio (sem SQL Server; SQLite temporario com dados sinteticos de resultado conhecido)."""
import csv
import hashlib
import itertools
import json
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
sps = pytest.importorskip("scipy.stats")

from tcclab import analise, relatorio  # noqa: E402
from tcclab.analise import Criterios, analisar, holm, p_minimo_wilcoxon, razao_medianas, wilcoxon_log_razao  # noqa: E402
from tcclab.cli import main  # noqa: E402
from tcclab.stats import resumir  # noqa: E402
from tcclab.store import Armazem  # noqa: E402

RAPIDO = Criterios(reamostragens=1000)   # bootstrap menor so para acelerar os testes (o padrao 10000 tem teste proprio)
AMB = dict(captured_at_utc="2026-10-04 00:00:00", sql_version="v", edition="e", product_version="p", compat_level=160,
           ce_version=160, cpu_count=1, physical_memory_mb=1, max_server_memory_mb=1, maxdop=0, cost_threshold=5,
           container_platform="Linux", emulacao_amd64_em_arm64=True, query_store_options=["READ_WRITE"], tool_version="0",
           tool_commit=None, python_version="3.14", python_lock_hash=None, driver="d", database="tcc_lab", avisos=[])


class Med:
    """Medicao minima aceita por Armazem.gravar_medicao."""
    def __init__(self, **v):
        base = dict(logical_reads=0, physical_reads=0, read_ahead_reads=0, lob_logical_reads=0, scan_count=1, cpu_ms=0,
                    elapsed_ms=0, compile_cpu_ms=0, compile_elapsed_ms=0, client_ms=1.0, rows=1, result_hash="h", io_raw="io",
                    time_raw="t", ts_utc="2026-10-04 00:00:01", cpu_us=None, elapsed_us=None, dmv_logical_reads=None,
                    io_por_tabela=[])
        base.update(v)
        self.__dict__.update(base)


def construir(caminho, consultas, regimes=("quente",), experimento="sint", seed=7, arm=None, aviso_run=False):
    """Cria (ou acrescenta a) um SQLite com 1 experimento. Cada consulta: dict(id, antipadrao, antes={metrica: lista},
    depois={metrica: lista}); `antes` e dividido ao meio entre os blocos A1 e A2 (desenho ABA)."""
    arm = arm or Armazem(caminho)
    env = arm.novo_ambiente(AMB)
    exp = arm.novo_experimento(experimento, {"x": 1}, env, seed, "2026-10-04 00:00:00")
    runs = {}
    for q in consultas:
        arm.garantir_consulta(q["id"], q["id"], "SELECT 1", q.get("antipadrao"))
        va = arm.nova_variante(q["id"], "antes", "original", "SELECT 1", None, None)
        vb = arm.nova_variante(q["id"], "depois", q.get("kind", "index"), "SELECT 1", "CREATE INDEX x", "DROP INDEX x")
        seq = 0
        for regime in regimes:
            for bloco, var, lado in (("A1", va, "antes"), ("B", vb, "depois"), ("A2", va, "antes")):
                seq += 1
                run = arm.novo_run(exp, var, seq, regime, bloco, "2026-10-04 00:00:00")
                runs[(q["id"], regime, bloco)] = run
                dados = q[lado]
                n = len(next(iter(dados.values())))
                fatia = slice(0, n // 2) if bloco == "A1" else slice(n // 2, n) if bloco == "A2" else slice(0, n)
                for rep, i in enumerate(range(n)[fatia], 1):
                    arm.gravar_medicao(run, rep, "measured", Med(**{m: v[i] for m, v in dados.items()}))
                arm.fechar_run(run, "concluido", "2026-10-04 00:01:00", ["atividade"] if aviso_run else [])
    arm.fechar_experimento(exp, "concluido", "2026-10-04 00:02:00", [])
    arm.db.commit()
    return arm, exp, runs


DDL_COLETOR = """
CREATE TABLE IF NOT EXISTS rodada (rodada TEXT PRIMARY KEY, iniciada_utc TEXT, compat INTEGER, maxdop INTEGER, parametros_json TEXT);
CREATE TABLE IF NOT EXISTS coleta_consulta (
  rodada TEXT, query_id TEXT, experiment_id INTEGER, tentativa INTEGER, status TEXT, equivalencia_json TEXT,
  nativo_ddl TEXT, notas_json TEXT, deriva_a1_a2 REAL, descartado_por_deriva INTEGER, duracao_s REAL, erro TEXT);
CREATE TABLE IF NOT EXISTS escrita_indice (rodada TEXT, query_id TEXT, tabela TEXT, indice TEXT, create_ddl TEXT, k INTEGER, criacao_ms REAL, paginas INTEGER);
CREATE TABLE IF NOT EXISTS escrita_medicao (rodada TEXT, query_id TEXT, tabela TEXT, indice TEXT, operacao TEXT, k INTEGER, rep INTEGER,
  logical_reads INTEGER, log_bytes INTEGER, cpu_ms INTEGER, elapsed_ms INTEGER);
"""


def coletor(caminho, exp_por_query, rodada="r", descartados=(), escrita=None):
    """Acrescenta as tabelas auxiliares do coletor. exp_por_query: {query_id: experiment_id}; descartados: ids de experimento
    com descartado_por_deriva=1; escrita: {query_id: {operacao: (lr_sem, lr_com, log_sem, log_com)}} (2 repeticoes cada)."""
    con = sqlite3.connect(caminho)
    con.executescript(DDL_COLETOR)
    con.execute("INSERT OR IGNORE INTO rodada VALUES (?, '2026-10-25', 160, 1, '{}')", (rodada,))
    for q, e in exp_por_query.items():
        con.execute("INSERT INTO coleta_consulta(rodada, query_id, experiment_id, tentativa, status, deriva_a1_a2, descartado_por_deriva) "
                    "VALUES (?, ?, ?, 1, 'concluido', ?, ?)", (rodada, q, e, 0.3 if e in descartados else 0.02, int(e in descartados)))
    for q, ops in (escrita or {}).items():
        con.execute("INSERT INTO escrita_indice VALUES (?, ?, 'dbo.T', 'ix_t', 'CREATE INDEX ix_t ON dbo.T(a)', 1000, 1500.0, 321)", (rodada, q))
        for op, (lr0, lr1, lg0, lg1) in ops.items():
            for rep in (1, 2):
                con.execute("INSERT INTO escrita_medicao VALUES (?, ?, 'dbo.T', 'sem indice', ?, 1000, ?, ?, ?, 5, 6)", (rodada, q, op, rep, lr0, lg0))
                con.execute("INSERT INTO escrita_medicao VALUES (?, ?, 'dbo.T', 'ix_t', ?, 1000, ?, ?, ?, 7, 8)", (rodada, q, op, rep, lr1, lg1))
    con.commit()
    con.close()


def serie(valor, n=20):
    return [valor] * n


def ruido(centro, n=40, dp=0.5, semente=1):
    rng = np.random.default_rng(semente)
    return [max(0, int(round(x))) for x in rng.normal(centro, dp, n)]


def consulta_fixa(i, fator=0.5, antipadrao=None, base_lr=1000, base_t=20):
    """Reducao exata por fator em TODAS as metricas (series constantes): razao = fator."""
    k = i + 1
    return dict(id=f"Q{k:02d}", antipadrao=antipadrao,
                antes=dict(logical_reads=serie(base_lr * k, 40), cpu_ms=serie(base_t * k, 40), elapsed_ms=serie(base_t * k, 40)),
                depois=dict(logical_reads=serie(int(base_lr * k * fator), 20), cpu_ms=serie(int(base_t * k * fator), 20),
                            elapsed_ms=serie(int(base_t * k * fator), 20)))


def construir_variantes(caminho, consultas, regimes=("quente",), experimento="multi", arm=None, query_id_sufixo=""):
    """Como `construir`, mas com varias variantes por consulta, no estilo do coletor: cada consulta traz
    `variantes=[(nome, kind, {metrica: lista}), ...]`, a primeira e a original (ABA: A1, demais, A2)."""
    arm = arm or Armazem(caminho)
    env = arm.novo_ambiente(AMB)
    exp = arm.novo_experimento(experimento, {"x": 1}, env, 7, "2026-10-04 00:00:00")
    for q in consultas:
        qid = q["id"] + query_id_sufixo
        arm.garantir_consulta(qid, qid, "SELECT 1", q.get("antipadrao"))
        vs = [arm.nova_variante(qid, nome, kind, "SELECT 1", None, None) for nome, kind, _ in q["variantes"]]
        seq = 0
        for regime in regimes:
            blocos = [("A1", 0)] + [(chr(ord("B") + i - 1), i) for i in range(1, len(vs))] + [("A2", 0)]
            for bloco, i in blocos:
                seq += 1
                run = arm.novo_run(exp, vs[i], seq, regime, bloco, "2026-10-04 00:00:00")
                dados = q["variantes"][i][2]
                if isinstance(dados, dict) and regime in dados:       # valores por regime
                    dados = dados[regime]
                n = len(next(iter(dados.values())))
                fatia = slice(0, n // 2) if bloco == "A1" else slice(n // 2, n) if bloco == "A2" else slice(0, n)
                for rep, k in enumerate(range(n)[fatia], 1):
                    arm.gravar_medicao(run, rep, "measured", Med(**{m: v[k] for m, v in dados.items()}))
                arm.fechar_run(run, "concluido", "2026-10-04 00:01:00", [])
    arm.fechar_experimento(exp, "concluido", "2026-10-04 00:02:00", [])
    arm.db.commit()
    return arm, exp


def var(nome, kind, lr, n=20, cpu=0):
    return (nome, kind, dict(logical_reads=serie(lr, n), cpu_ms=serie(cpu, n), elapsed_ms=serie(cpu, n)))


def consulta_completa(i, antipadrao="AP1", com_reescrita=True, base=1000):
    """original; reescrita 0,7; indice 0,4; prescricao (reescrita+indice) 0,1; nativo 0,08 (so se `com_reescrita`)."""
    b = base * (i + 1)
    vs = [var("original", "original", b, 40)]
    if com_reescrita:
        vs += [var("reescrita", "reescrita", int(b * 0.7)), var("indice", "indice", int(b * 0.4)),
               var("reescrita+indice", "prescricao", int(b * 0.1)), var("nativo", "nativo", int(b * 0.08))]
    else:
        vs += [var("indice", "indice", int(b * 0.25))]            # AP4: sem reescrita, o indice e a prescricao
    return dict(id=f"Q{i + 1:02d}", antipadrao=antipadrao, variantes=vs)


@pytest.fixture()
def banco50(tmp_path):
    """8 consultas, todas com reducao de 50% em leituras logicas, CPU e decorrido."""
    arm, exp, runs = construir(tmp_path / "b50.sqlite", [consulta_fixa(i) for i in range(8)])
    arm.fechar()
    return tmp_path / "b50.sqlite"


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def linhas(analise_, nome, **filtro):
    return [r for r in getattr(analise_, nome) if all(r[k] == v for k, v in filtro.items())]


# ----------------------------------------------------------------------------------------- primitivas
def test_holm_contra_calculo_manual():
    # manual: ordenados 0,01 / 0,03 / 0,04 -> 3*0,01=0,03; 2*0,03=0,06; 1*0,04=0,04 -> max acumulado 0,06
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.001, 0.2, 0.5]) == pytest.approx([0.003, 0.4, 0.5])
    assert holm([0.4, 0.4, 0.4]) == pytest.approx([1.0, 1.0, 1.0])   # teto em 1
    assert holm([0.2, 0.3, 0.9]) == pytest.approx([0.6, 0.6, 0.9])
    # familia declarada (3 metricas) com um p ausente: multiplicadores continuam 3, 2, 1
    assert holm([0.01, None, 0.04], m=3) == [pytest.approx(0.03), None, pytest.approx(0.08)]
    # definicao independente: p_(i) ajustado = max_{j<=i} (m-j+1) p_(j)
    p = [0.012, 0.03, 0.021, 0.5]
    ordem = sorted(range(4), key=lambda i: p[i])
    esperado = [None] * 4
    for k, i in enumerate(ordem):
        esperado[i] = min(1, max((4 - j) * p[ordem[j]] for j in range(k + 1)))
    assert holm(p) == pytest.approx(esperado)


def test_percentis_do_ic_seguem_o_nivel():
    x = np.arange(1001.0)
    assert analise._percentis(x, 0.95) == pytest.approx((25.0, 975.0))     # 2,5% e 97,5%
    assert analise._percentis(x, 0.80) == pytest.approx((100.0, 900.0))
    a = analise._medianas_bootstrap(np.array([1.0, 2.0, 3.0, 4.0, 100.0]), analise.rng_para(1, "x"), 2000)
    assert a.shape == (2000,) and a.min() >= 1.0 and a.max() <= 100.0


def test_conexao_de_analise_e_somente_leitura(banco50):
    con = analise._abrir_somente_leitura(banco50)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("CREATE TABLE intruso (a)")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("DELETE FROM measurement")
    con.close()


def test_razao_com_mediana_zero_sem_dividir_por_zero():
    r = razao_medianas(0, 0, 0.5)
    assert (r.bruta, r.reg, r.regra, r.reducao_pct) == (1.0, 1.0, "ambas_zero", 0.0)
    r = razao_medianas(13, 0, 0.5)
    assert (r.bruta, r.regra, r.reducao_pct) == (0.0, "depois_zero", 100.0)
    assert r.reg == pytest.approx(0.5 / 13)                      # cota superior: piso / antes
    r = razao_medianas(0, 2, 0.5)
    assert (r.bruta, r.reducao_pct, r.regra) == (None, None, "antes_zero")
    assert r.reg == pytest.approx(4.0)                           # cota inferior: depois / piso
    r = razao_medianas(10, 5)
    assert (r.bruta, r.reg, r.regra) == (0.5, 0.5, "")           # medianas nao nulas nunca mudam


def test_p_minimo_do_wilcoxon_exato_por_n():
    assert [p_minimo_wilcoxon(n) for n in (1, 2, 3, 5, 6)] == pytest.approx([1.0, 0.5, 0.25, 0.0625, 0.03125])
    assert p_minimo_wilcoxon(0) == 1.0 and p_minimo_wilcoxon(5, "less") == pytest.approx(1 / 32)
    # n = 5 todos no mesmo sentido: o scipy confirma que 0,0625 e o menor p possivel
    assert sps.wilcoxon([-0.7, -0.6, -0.5, -0.4, -0.3]).pvalue == pytest.approx(0.0625)


def p_exato_bruto(d):
    """Teste de permutacao dos sinais (exato, com ranks medios nos empates), bilateral por min(T+, T-)."""
    d = np.round(np.asarray(d, float), 12)       # log(1,25) e -log(0,8) so empatam depois de arredondar
    d = d[d != 0]
    r = sps.rankdata(np.abs(d))
    obs = min(r[d > 0].sum(), r[d < 0].sum())
    cont = 0
    for s in itertools.product((False, True), repeat=len(d)):
        s = np.array(s)
        cont += min(r[s].sum(), r[~s].sum()) <= obs + 1e-9
    return cont / 2 ** len(d)


@pytest.mark.parametrize("razoes", [
    [0.5, 0.6, 0.45, 0.8, 0.7, 0.3, 1.2],                 # sem empates, um aumento
    [0.5, 0.5, 0.25, 0.8, 0.8, 0.3, 1.25, 0.5],           # empates em |log r| (0,5 e 0,8 repetidos)
    [0.5, 1.0, 1.0, 0.7, 0.9, 0.35, 0.6],                 # zeros (razao 1) descartados
    [0.5] * 6,                                            # todas iguais: p = 2/2^6
])
def test_wilcoxon_log_razao_contra_enumeracao_manual(razoes):
    w = wilcoxon_log_razao(razoes, RAPIDO)
    assert w["p"] == pytest.approx(p_exato_bruto(np.log(razoes)))
    assert w["n_zeros"] == sum(r == 1.0 for r in razoes)


def test_wilcoxon_unilateral_de_reducao_contra_enumeracao_manual():
    razoes = [0.5, 0.6, 0.45, 0.8, 0.7, 0.3, 1.2]
    d = np.round(np.log(razoes), 12)
    r = sps.rankdata(np.abs(d))
    obs = r[d > 0].sum()                                           # T+: soma dos postos dos aumentos
    p_less = sum(r[np.array(sg)].sum() <= obs + 1e-9 for sg in itertools.product((False, True), repeat=len(d))) / 2 ** len(d)
    w = wilcoxon_log_razao(razoes, Criterios(reamostragens=1000, alternativa="less"))
    assert w["p"] == pytest.approx(p_less) and w["p_minimo"] == pytest.approx(1 / 128)
    assert wilcoxon_log_razao(razoes, RAPIDO)["p"] == pytest.approx(min(1, 2 * p_less))     # bilateral = 2 x unilateral aqui


def test_wilcoxon_sem_diferencas_e_sem_dados():
    assert wilcoxon_log_razao([1.0, 1.0, 1.0], RAPIDO)["metodo_p"] == "sem_diferencas"
    assert wilcoxon_log_razao([1.0, 1.0, 1.0], RAPIDO)["p"] == 1.0
    assert wilcoxon_log_razao([], RAPIDO)["p"] is None


def test_tukey_de_analise_concorda_com_stats_resumir(tmp_path):
    vals = [10, 10, 11, 10, 12, 10, 11, 90, 10, 11]
    antes = dict(logical_reads=serie(100, 20), cpu_ms=vals + vals, elapsed_ms=serie(10, 20))
    depois = dict(logical_reads=serie(50, 10), cpu_ms=serie(5, 10), elapsed_ms=serie(5, 10))
    arm, exp, _ = construir(tmp_path / "t.sqlite", [dict(id="Q1", antes=antes, depois=depois)])
    arm.fechar()
    a = analisar(tmp_path / "t.sqlite", [exp], RAPIDO)
    flagged = {(r["run_id"], r["rep"]) for r in a.atipicos if r["metrica"] == "cpu_ms" and r["variante"] == "antes"}
    esperado = resumir([float(v) for v in vals + vals]).atipicos
    assert len(flagged) == len(esperado) == 2                      # o 90 de cada bloco
    iqr_lim = [(r["limite_inf"], r["limite_sup"]) for r in a.atipicos if r["metrica"] == "cpu_ms"][0]
    r = resumir([float(v) for v in vals + vals])
    assert iqr_lim == (r.q1 - 1.5 * (r.q3 - r.q1), r.q3 + 1.5 * (r.q3 - r.q1))


# ----------------------------------------------------------------------------------------- resultado conhecido
def test_reducao_fixa_de_50_pct_da_razao_0_5(banco50):
    a = analisar(banco50, [1], RAPIDO)
    comps = linhas(a, "comparacoes", metrica="logical_reads")
    assert len(comps) == 8
    for c in comps:
        assert c["razao"] == 0.5 and c["razao_reg"] == 0.5 and c["reducao_pct"] == 50.0
        assert (c["ic_lo"], c["ic_hi"]) == (0.5, 0.5)               # series constantes: IC degenerado
        assert c["regra_zero"] == "" and c["atipicos_antes"] == 0 == c["atipicos_depois"]
        assert c["razao_sem_atipicos"] == 0.5 and c["deriva_base_pct"] == 0.0
    assert all(v["melhoria_significativa"] and v["reducao_ok"] and v["ic_exclui_1"] for v in a.veredictos)
    assert all(not v["veredito_muda_sem_atipicos"] for v in a.veredictos)
    todas = linhas(a, "agregados", analise="principal", estrato_tipo="todas", metrica="logical_reads")[0]
    assert todas["n_consultas"] == 8 and todas["reducao_mediana_pct"] == 50.0 and todas["razao_mediana"] == 0.5
    assert todas["razao_geometrica"] == pytest.approx(0.5)
    assert (todas["reducao_ic_lo"], todas["reducao_ic_hi"]) == (50.0, 50.0) and todas["ic_confiavel"]
    # todas as diferencas no mesmo sentido, n = 8: p = 2/2^8 em cada metrica; Holm de 3 metricas: 3 * p
    assert todas["p_bruto"] == pytest.approx(2 / 256) and todas["p_minimo"] == pytest.approx(2 / 256)
    assert todas["p_holm"] == pytest.approx(3 * 2 / 256) and todas["significativo_holm"] is True
    assert todas["n_melhoria_significativa"] == 8 and not todas["poder_insuficiente"]
    for m in ("cpu_ms", "elapsed_ms"):
        t = linhas(a, "agregados", analise="principal", estrato_tipo="todas", metrica=m)[0]
        assert t["p_holm"] == pytest.approx(0.0234375) and t["reducao_mediana_pct"] == 50.0


def test_holm_do_agregado_igual_ao_calculo_manual_sobre_p_do_scipy(tmp_path):
    fatores_cpu = [0.5, 0.6, 0.7, 0.8, 0.9, 1.1, 0.55, 0.65, 0.95]       # razoes exatas (series constantes)
    fatores_ela = [1.1, 0.9, 1.2, 0.95, 1.05, 0.85, 1.15, 0.97, 1.03]
    cons = []
    for i, (fc, fe) in enumerate(zip(fatores_cpu, fatores_ela)):
        k = i + 1
        cons.append(dict(id=f"Q{k:02d}", antes=dict(logical_reads=serie(1000 * k, 20), cpu_ms=serie(1000 * k, 20), elapsed_ms=serie(1000 * k, 20)),
                         depois=dict(logical_reads=serie(500 * k, 10), cpu_ms=serie(round(1000 * k * fc), 10), elapsed_ms=serie(round(1000 * k * fe), 10))))
    arm, exp, _ = construir(tmp_path / "h.sqlite", cons)
    arm.fechar()
    a = analisar(tmp_path / "h.sqlite", [exp], RAPIDO)
    ps = {}
    for m, f in (("logical_reads", [0.5] * 9), ("cpu_ms", fatores_cpu), ("elapsed_ms", fatores_ela)):
        ps[m] = sps.wilcoxon(np.round(np.log(f), 12)).pvalue
        linha = linhas(a, "agregados", analise="principal", estrato_tipo="todas", metrica=m)[0]
        assert linha["p_bruto"] == pytest.approx(ps[m])
    esperado = dict(zip(ps, holm(list(ps.values()), m=3)))
    for m, pe in esperado.items():
        assert linhas(a, "agregados", analise="principal", estrato_tipo="todas", metrica=m)[0]["p_holm"] == pytest.approx(pe)
    # CPU: 8 de 9 reducoes -> efeito = (1 - mediana das razoes) * 100
    cpu = linhas(a, "agregados", analise="principal", estrato_tipo="todas", metrica="cpu_ms")[0]
    assert cpu["reducao_mediana_pct"] == pytest.approx((1 - float(np.median(fatores_cpu))) * 100)
    assert cpu["n_reduz"] == 8 and cpu["n_aumenta"] == 1


def test_medianas_zero_nas_metricas_de_tempo(tmp_path):
    cons = [
        dict(id="Q1", antes=dict(logical_reads=serie(100, 20), cpu_ms=serie(0, 20), elapsed_ms=serie(13, 20)),     # ambas zero (CPU)
             depois=dict(logical_reads=serie(10, 10), cpu_ms=serie(0, 10), elapsed_ms=serie(0, 10))),             # elapsed 13 -> 0
        dict(id="Q2", antes=dict(logical_reads=serie(100, 20), cpu_ms=serie(0, 20), elapsed_ms=serie(5, 20)),     # antes zero, depois > 0
             depois=dict(logical_reads=serie(10, 10), cpu_ms=serie(2, 10), elapsed_ms=serie(5, 10))),
    ]
    arm, exp, _ = construir(tmp_path / "z.sqlite", cons)
    arm.fechar()
    a = analisar(tmp_path / "z.sqlite", [exp], RAPIDO)
    c = {(r["query_id"], r["metrica"]): r for r in a.comparacoes}
    assert c[("Q1", "cpu_ms")]["razao"] == 1.0 and c[("Q1", "cpu_ms")]["regra_zero"] == "ambas_zero"
    assert c[("Q1", "elapsed_ms")]["razao"] == 0.0 and c[("Q1", "elapsed_ms")]["reducao_pct"] == 100.0
    assert c[("Q1", "elapsed_ms")]["regra_zero"] == "depois_zero"
    assert c[("Q1", "elapsed_ms")]["razao_reg"] == pytest.approx(0.5 / 13)
    assert c[("Q2", "cpu_ms")]["razao"] is None and c[("Q2", "cpu_ms")]["reducao_pct"] is None
    assert c[("Q2", "cpu_ms")]["razao_reg"] == pytest.approx(4.0) and c[("Q2", "cpu_ms")]["regra_zero"] == "antes_zero"
    for r in a.comparacoes + a.resumos + a.agregados:                     # nada de NaN/inf escapa
        for v in r.values():
            assert not (isinstance(v, float) and (np.isnan(v) or np.isinf(v)))
    json.loads(analise.para_json(a))                                       # JSON valido (sem NaN)
    ag = linhas(a, "agregados", analise="principal", estrato_tipo="todas", metrica="cpu_ms")[0]
    assert ag["n_consultas"] == 2 and ag["n_efetivo"] == 1                   # Q1 (razao exatamente 1) fica fora do Wilcoxon...
    assert "descartada" in ag["observacao"]                                   # ...e isso e dito


def test_n_pequeno_avisa_e_nao_esconde(tmp_path):
    arm, exp, _ = construir(tmp_path / "p.sqlite", [consulta_fixa(i) for i in range(3)])
    arm.fechar()
    a = analisar(tmp_path / "p.sqlite", [exp], RAPIDO)
    ag = linhas(a, "agregados", analise="principal", estrato_tipo="todas", metrica="logical_reads")[0]
    assert ag["n_consultas"] == 3 and ag["p_minimo"] == pytest.approx(0.25) and ag["p_bruto"] == pytest.approx(0.25)
    assert ag["poder_insuficiente"] is True and ag["significativo_holm"] is False and ag["ic_confiavel"] is False
    assert "NAO pode rejeitar H0" in ag["observacao"] and "exploratorio" in ag["observacao"]
    assert any("n_min_consultas" in av for av in a.avisos)
    # a reducao e o IC continuam reportados (nao se esconde o efeito, so o poder do teste)
    assert ag["reducao_mediana_pct"] == 50.0 and ag["reducao_ic_lo"] is not None


@pytest.mark.parametrize("n,poder_bruto,sobrevive_holm", [(5, False, False), (6, True, False), (7, True, True)])
def test_limiar_de_poder_do_wilcoxon_por_n(tmp_path, n, poder_bruto, sobrevive_holm):
    arm, exp, _ = construir(tmp_path / f"n{n}.sqlite", [consulta_fixa(i) for i in range(n)])
    arm.fechar()
    ag = linhas(analisar(tmp_path / f"n{n}.sqlite", [exp], RAPIDO), "agregados", analise="principal", estrato_tipo="todas",
                metrica="logical_reads")[0]
    assert (not ag["poder_insuficiente"]) is poder_bruto
    assert (ag["p_holm"] < 0.05) is sobrevive_holm
    if poder_bruto and not sobrevive_holm:
        assert "Holm" in ag["observacao"]


def test_reprodutibilidade_mesma_semente_e_ordem_dos_experimentos(tmp_path):
    cons = [dict(id=f"Q{i}", antes=dict(logical_reads=serie(1000, 40), cpu_ms=ruido(20, 40, 3, i), elapsed_ms=ruido(25, 40, 4, i + 9)),
                 depois=dict(logical_reads=serie(500, 20), cpu_ms=ruido(14, 20, 3, i + 3), elapsed_ms=ruido(18, 20, 4, i + 5)))
            for i in range(6)]
    arm, e1, _ = construir(tmp_path / "r.sqlite", cons[:3], experimento="e1")
    _, e2, _ = construir(tmp_path / "r.sqlite", cons[3:], experimento="e2", arm=arm)
    arm.fechar()
    sem_meta = lambda a: {k: v for k, v in a.como_dict().items() if k != "meta"}  # noqa: E731
    a1 = analisar(tmp_path / "r.sqlite", [e1, e2], Criterios(reamostragens=2000, semente=11))
    a2 = analisar(tmp_path / "r.sqlite", [e1, e2], Criterios(reamostragens=2000, semente=11))
    a3 = analisar(tmp_path / "r.sqlite", [e2, e1], Criterios(reamostragens=2000, semente=11))
    assert sem_meta(a1) == sem_meta(a2)
    assert [r for r in a1.comparacoes] == [r for r in a3.comparacoes]            # ordem dos experimentos nao muda nada
    assert a1.agregados == a3.agregados
    a4 = analisar(tmp_path / "r.sqlite", [e1, e2], Criterios(reamostragens=2000, semente=12))
    ic = lambda a: [(r["ic_lo"], r["ic_hi"]) for r in a.comparacoes if r["metrica"] == "cpu_ms"]  # noqa: E731
    assert ic(a1) != ic(a4)                                                      # outra semente, outro IC
    assert [r["razao"] for r in a1.comparacoes] == [r["razao"] for r in a4.comparacoes]   # a estimativa pontual nao depende da semente


def test_ic_bootstrap_cobre_a_razao_conhecida_com_ruido(tmp_path):
    cons = [dict(id="Q1", antes=dict(logical_reads=serie(1000, 60), cpu_ms=ruido(2000, 60, 100, 1), elapsed_ms=ruido(2100, 60, 100, 2)),
                 depois=dict(logical_reads=serie(500, 30), cpu_ms=ruido(1000, 30, 100, 3), elapsed_ms=ruido(1050, 30, 100, 4)))]
    arm, exp, _ = construir(tmp_path / "c.sqlite", cons)
    arm.fechar()
    a = analisar(tmp_path / "c.sqlite", [exp], Criterios())                        # padrao: 10000 reamostragens
    assert a.criterios.reamostragens == 10000
    c = linhas(a, "comparacoes", metrica="cpu_ms")[0]
    assert c["ic_lo"] < 0.5 < c["ic_hi"] and (c["ic_hi"] - c["ic_lo"]) < 0.1       # cobre a razao verdadeira (0,5), IC estreito
    assert c["ic_lo"] < c["razao"] < c["ic_hi"]


def test_resumo_usa_todas_as_repeticoes_do_banco(tmp_path):
    vals = ruido(2000, 40, 100, 5)
    cons = [dict(id="Q1", antes=dict(logical_reads=serie(10, 40), cpu_ms=vals), depois=dict(logical_reads=serie(5, 20), cpu_ms=serie(5, 20)))]
    arm, exp, _ = construir(tmp_path / "u.sqlite", cons)
    arm.fechar()
    crit = Criterios(reamostragens=700, semente=3)
    a = analisar(tmp_path / "u.sqlite", [exp], crit)
    s = linhas(a, "resumos", metrica="cpu_ms", variante="antes")[0]
    x = np.array(vals, float)
    assert (s["n"], s["minimo"], s["maximo"]) == (40, x.min(), x.max()) and s["mediana"] == float(np.median(x))
    assert s["media"] == pytest.approx(x.mean()) and s["dp"] == pytest.approx(x.std(ddof=1)) and s["cv"] == pytest.approx(x.std(ddof=1) / x.mean())
    lo, hi = analise.ic_mediana(x, crit, exp, "Q1", s["variant_id"], "quente", "cpu_ms")   # mesma chave, mesma semente
    assert (s["ic_lo"], s["ic_hi"]) == (lo, hi) and s["meia_largura_pct"] == pytest.approx((hi - lo) / 2 / s["mediana"] * 100)
    assert s["shapiro_p"] == pytest.approx(float(sps.shapiro(x).pvalue))


def test_sensibilidade_marca_atipicos_sem_descartar(tmp_path):
    ant = [10] * 9 + [12] * 9 + [90, 90]       # mediana 12 com os 90 e 11 sem eles (Tukey sinaliza so os 90)
    cons = [dict(id="Q1", antes=dict(logical_reads=serie(100, 20), cpu_ms=ant, elapsed_ms=serie(10, 20)),
                 depois=dict(logical_reads=serie(50, 10), cpu_ms=serie(5, 10), elapsed_ms=serie(5, 10)))]
    arm, exp, _ = construir(tmp_path / "s.sqlite", cons)
    arm.fechar()
    a = analisar(tmp_path / "s.sqlite", [exp], RAPIDO)
    c = linhas(a, "comparacoes", metrica="cpu_ms")[0]
    n_atip = c["atipicos_antes"]
    assert n_atip == 2 and c["n_antes"] == 20                                      # as 20 medidas continuam na analise principal
    assert c["mediana_antes"] == float(np.median(ant))                        # principal usa TODAS as medidas
    com_90 = float(np.median(ant))
    sem_90 = float(np.median([v for v in ant if v < 50]))
    assert c["razao"] == pytest.approx(5 / com_90) and c["razao_sem_atipicos"] == pytest.approx(5 / sem_90)
    assert c["razao"] != c["razao_sem_atipicos"]
    out = [r for r in a.atipicos if r["metrica"] == "cpu_ms" and r["variante"] == "antes"]
    assert sorted(r["valor"] for r in out)[-2:] == [90.0, 90.0]                    # os 90 estao listados com run_id e rep
    assert all(r["run_id"] > 0 and r["rep"] > 0 for r in out)
    s = linhas(a, "resumos", metrica="cpu_ms", variante="antes")[0]
    assert s["n"] == 20 and s["maximo"] == 90.0 and s["atipicos"] == n_atip      # maximo continua sendo 90 no resumo


def test_determinismo_das_leituras_logicas(tmp_path):
    variavel = [100, 100, 101, 100] * 5
    cons = [dict(id="Q1", antes=dict(logical_reads=serie(100, 20), cpu_ms=serie(5, 20), elapsed_ms=serie(5, 20)),
                 depois=dict(logical_reads=serie(50, 10), cpu_ms=serie(3, 10), elapsed_ms=serie(3, 10))),
            dict(id="Q2", antes=dict(logical_reads=variavel, cpu_ms=serie(5, 20), elapsed_ms=serie(5, 20)),
                 depois=dict(logical_reads=serie(50, 10), cpu_ms=serie(3, 10), elapsed_ms=serie(3, 10)))]
    arm, exp, _ = construir(tmp_path / "d.sqlite", cons)
    arm.fechar()
    a = analisar(tmp_path / "d.sqlite", [exp], RAPIDO)
    d = {(r["query_id"], r["variante"]): r for r in a.determinismo}
    assert d[("Q1", "antes")]["deterministico"] is True and d[("Q1", "antes")]["dp"] == 0 and d[("Q1", "antes")]["alerta"] == ""
    q2 = d[("Q2", "antes")]
    assert q2["deterministico"] is False and q2["dp"] > 0 and q2["valores_distintos"] == 2
    assert "investigar" in q2["alerta"] and "paralelismo" in q2["alerta"] and "read-ahead" in q2["alerta"]
    assert any("NAO deterministicas" in av for av in a.avisos)


def test_estratificacao_por_antipadrao_do_catalogo_e_por_argumento(tmp_path):
    cons = [consulta_fixa(i, antipadrao="SARG" if i < 4 else "LIKE") for i in range(7)] + [consulta_fixa(7)]
    arm, exp, _ = construir(tmp_path / "e.sqlite", cons)
    arm.fechar()
    a = analisar(tmp_path / "e.sqlite", [exp], RAPIDO)
    n = {r["estrato"]: r["n_consultas"] for r in linhas(a, "agregados", analise="principal", estrato_tipo="antipadrao", metrica="logical_reads")}
    assert n == {"SARG": 4, "LIKE": 3, "(sem antipadrao)": 1}
    # o argumento prevalece sobre o catalogo e preenche as que faltam
    b = analisar(tmp_path / "e.sqlite", [exp], RAPIDO, antipadroes={"Q08": "LIKE", "Q01": "FUNC"})
    n2 = {r["estrato"]: r["n_consultas"] for r in linhas(b, "agregados", analise="principal", estrato_tipo="antipadrao", metrica="logical_reads")}
    assert n2 == {"SARG": 3, "LIKE": 4, "FUNC": 1}
    sarg = linhas(a, "agregados", analise="principal", estrato_tipo="antipadrao", estrato="SARG", metrica="logical_reads")[0]
    assert sarg["n_consultas"] == 4 and sarg["p_minimo"] == pytest.approx(2 / 16) and sarg["poder_insuficiente"]
    assert sarg["n_melhoria_significativa"] == 4


def test_criterio_de_melhoria_parametrizado(tmp_path):
    # Q1: reducao de 15% (nao atinge 20%); Q2: 30%
    cons = [consulta_fixa(0, fator=0.85), consulta_fixa(1, fator=0.7)]
    arm, exp, runs = construir(tmp_path / "k.sqlite", cons)
    arm.fechar()
    a = analisar(tmp_path / "k.sqlite", [exp], RAPIDO)
    v = {r["query_id"]: r for r in a.veredictos}
    assert not v["Q01"]["melhoria_significativa"] and v["Q01"]["reducao_ok"] is False
    assert v["Q02"]["melhoria_significativa"]
    b = analisar(tmp_path / "k.sqlite", [exp], Criterios(reamostragens=1000, reducao_minima_pct=10.0))
    assert all(r["melhoria_significativa"] for r in b.veredictos)                    # limiar parametrizado
    assert {r["criterio_escrita"] for r in b.veredictos} == {"sem_dados"}            # sem escrita_medicao: NAO avaliado, e dito
    assert Criterios().degradacao_escrita_max_pct is None                            # padrao = PRE-REGISTRO.md (sem limiar)


def test_custo_de_escrita_so_reporta_razao_e_limiar_opcional(tmp_path):
    arm, exp, _ = construir(tmp_path / "w.sqlite", [consulta_fixa(0, fator=0.5), consulta_fixa(1, fator=0.5)])
    arm.fechar()
    # Q01: INSERT 7.800 -> 11.000 leituras (+41%), UPDATE 217 -> 12.470 (x57,5); Q02: tudo +5%
    coletor(tmp_path / "w.sqlite", {"Q01": exp, "Q02": exp}, escrita={
        "Q01": {"insert": (7800, 11000, 1865172, 1994492), "update": (217, 12470, 120120, 364120), "delete": (217, 10137, 1564652, 1505700)},
        "Q02": {"insert": (1000, 1050, 100, 105), "update": (1000, 1050, 100, 105), "delete": (1000, 1050, 100, 105)}})
    a = analisar(tmp_path / "w.sqlite", [exp], RAPIDO)
    e = {(r["query_id"], r["operacao"], r["metrica"]): r for r in a.escrita}
    assert len(a.escrita) == 2 * 3 * 4                                                # 2 consultas x 3 operacoes x 4 metricas
    ins = e[("Q01", "insert", "logical_reads")]
    assert (ins["mediana_sem"], ins["mediana_com"]) == (7800.0, 11000.0) and ins["razao"] == pytest.approx(11000 / 7800)
    assert ins["aumento_pct"] == pytest.approx((11000 / 7800 - 1) * 100) and ins["indice_paginas"] == 321 and ins["criacao_ms"] == 1500.0
    assert e[("Q01", "update", "logical_reads")]["razao"] == pytest.approx(12470 / 217)
    assert e[("Q01", "delete", "log_bytes")]["razao"] == pytest.approx(1505700 / 1564652)    # razao < 1 e reportada como esta
    assert e[("Q01", "update", "cpu_ms")]["razao"] == pytest.approx(7 / 5) and ins["n_sem"] == ins["n_com"] == 2
    # padrao (sem limiar): a razao de escrita nao reprova a melhoria, so e reportada
    v = {r["query_id"]: r for r in a.veredictos}
    assert v["Q01"]["criterio_escrita"] == "sem_limiar" and v["Q01"]["escrita_razao"] == pytest.approx(12470 / 217)
    assert v["Q01"]["melhoria_significativa"] is True and v["Q02"]["escrita_razao"] == pytest.approx(1.05)
    # limiar opcional (o 10% do relatorio 16): Q01 degrada, Q02 (+5%) passa
    b = analisar(tmp_path / "w.sqlite", [exp], Criterios(reamostragens=1000, degradacao_escrita_max_pct=10.0))
    vb = {r["query_id"]: r for r in b.veredictos}
    assert vb["Q01"]["criterio_escrita"] == "degradado" and vb["Q01"]["melhoria_significativa"] is False
    assert vb["Q02"]["criterio_escrita"] == "ok" and vb["Q02"]["melhoria_significativa"] is True
    # outra metrica de escrita no veredito: bytes de log (Q01: maximo das razoes por operacao = 364120/120120)
    c = analisar(tmp_path / "w.sqlite", [exp], Criterios(reamostragens=1000, metrica_escrita="log_bytes"))
    assert {r["query_id"]: r for r in c.veredictos}["Q01"]["escrita_razao"] == pytest.approx(364120 / 120120)
    assert Criterios.de_dict(json.loads('{"degradacao_escrita_max_pct": null}')).degradacao_escrita_max_pct is None
    with pytest.raises(ValueError):
        Criterios(degradacao_escrita_max_pct=-1)
    with pytest.raises(ValueError):
        Criterios(metrica_escrita="logical_writes")


def test_metricas_de_tempo_em_microssegundos_por_configuracao(tmp_path):
    cons = [dict(id=f"Q{i}", antes=dict(logical_reads=serie(1000, 40), cpu_us=ruido(13000, 40, 400, i), elapsed_us=ruido(14000, 40, 400, i + 7)),
                 depois=dict(logical_reads=serie(500, 20), cpu_us=ruido(40, 20, 3, i + 2), elapsed_us=ruido(60, 20, 3, i + 4))) for i in range(7)]
    arm, exp, _ = construir(tmp_path / "us.sqlite", cons)
    arm.fechar()
    crit = Criterios(reamostragens=500, metricas_teste=("logical_reads", "cpu_us", "elapsed_us"))
    a = analisar(tmp_path / "us.sqlite", [exp], crit)
    ag = {r["metrica"]: r for r in linhas(a, "agregados", analise="principal", estrato_tipo="todas")}
    assert set(ag) == {"logical_reads", "cpu_us", "elapsed_us"}                 # familia do Holm = as tres metricas declaradas
    assert all(r["p_bruto"] == pytest.approx(2 / 128) and r["p_holm"] == pytest.approx(6 / 128) for r in ag.values())   # n = 7
    assert ag["cpu_us"]["reducao_mediana_pct"] > 99 and all(c["regra_zero"] == "" for c in a.comparacoes if c["metrica"] == "cpu_us")
    # cpu_ms/elapsed_ms ficam 0 (padrao do helper): ambas_zero, razao 1, sem dividir por zero; e cpu_us None em banco sem us nao quebra
    assert {c["regra_zero"] for c in a.comparacoes if c["metrica"] == "cpu_ms"} == {"ambas_zero"}


def test_criterio_ic_precisa_excluir_um(tmp_path):
    # reducao mediana de 20% mas com ruido enorme: o IC95% da razao inclui 1
    rng = np.random.default_rng(3)
    antes = [int(x) for x in rng.integers(1, 200, 40)]
    depois = [int(x) for x in rng.integers(1, 200, 20)]
    arm, exp, _ = construir(tmp_path / "i.sqlite", [dict(id="Q1", antes=dict(logical_reads=antes), depois=dict(logical_reads=depois))])
    arm.fechar()
    a = analisar(tmp_path / "i.sqlite", [exp], Criterios(reamostragens=1000, metricas_teste=("logical_reads",), reducao_minima_pct=0.0))
    v = a.veredictos[0]
    assert v["ic_exclui_1"] is False and v["melhoria_significativa"] is False


def test_query_id_repetido_entre_experimentos_nao_vira_replicata(tmp_path):
    arm, e1, _ = construir(tmp_path / "dup.sqlite", [consulta_fixa(0)], experimento="a")
    _, e2, _ = construir(tmp_path / "dup.sqlite", [consulta_fixa(0)], experimento="b", arm=arm)
    arm.fechar()
    a = analisar(tmp_path / "dup.sqlite", [e1, e2], RAPIDO)
    assert {c["consulta"] for c in a.consultas} == {f"Q01 (exp. {e1})", f"Q01 (exp. {e2})"}
    assert any("query_id repetido" in av for av in a.avisos)


def test_runs_com_aviso_de_atividade_sao_sinalizados(tmp_path):
    arm, exp, _ = construir(tmp_path / "av.sqlite", [consulta_fixa(0)], aviso_run=True)
    arm.fechar()
    a = analisar(tmp_path / "av.sqlite", [exp], RAPIDO)
    assert any("segundo plano" in av for av in a.avisos)
    assert all(r["runs_com_aviso"] == r["n_runs"] for r in a.resumos)


def test_criterios_validacao_e_serializacao(tmp_path):
    c = Criterios(metricas_teste=("logical_reads", "cpu_us"), metricas_resumo=("logical_reads", "cpu_us"), reamostragens=500)
    assert Criterios.de_dict(json.loads(json.dumps(c.como_dict()))) == c and c.sha() == Criterios.de_dict(c.como_dict()).sha()
    assert Criterios().sha() != c.sha()
    for ruim in (dict(alfa=1.5), dict(reamostragens=10), dict(alternativa="x"), dict(metricas_teste=("nao_existe",)), dict(semente=-1)):
        with pytest.raises(ValueError):
            Criterios(**ruim)
    with pytest.raises(ValueError, match="desconhecido"):
        Criterios.de_dict({"foo": 1})
    arq = tmp_path / "c.json"
    arq.write_text(json.dumps({"reducao_minima_pct": 25, "metricas_teste": ["logical_reads"]}))
    assert Criterios.de_arquivo(arq).reducao_minima_pct == 25 and Criterios.de_arquivo(arq).metricas_teste == ("logical_reads",)


# ----------------------------------------------------------------------------------------- somente leitura, sem rede
def test_analise_nao_altera_o_banco_nem_abre_rede(banco50, tmp_path, monkeypatch):
    def proibido(*a, **k):
        raise AssertionError("tentativa de conexao de rede")
    monkeypatch.setattr(socket.socket, "connect", proibido)
    antes = sha(banco50)
    a = analisar(banco50, [1], RAPIDO)
    relatorio.gerar_relatorio(a, tmp_path / "out", figuras=False)
    assert sha(banco50) == antes
    # esquema de outra versao e recusado
    outro = tmp_path / "v9.sqlite"
    shutil.copy(banco50, outro)
    con = sqlite3.connect(outro)
    con.execute("PRAGMA user_version = 9")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="esquema v9"):
        analisar(outro, [1], RAPIDO)
    with pytest.raises(FileNotFoundError):
        analisar(tmp_path / "nao_existe.sqlite", [1], RAPIDO)
    with pytest.raises(KeyError):
        analisar(banco50, [99], RAPIDO)


def test_modulos_de_analise_nao_importam_o_driver_do_sql_server():
    codigo = ("import sys, tcclab.analise, tcclab.relatorio; "
              "assert 'tcclab.driver' not in sys.modules and 'pytds' not in sys.modules, sorted(m for m in sys.modules if 'tds' in m or 'driver' in m)")
    r = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_gravar_no_banco_usa_tabela_auxiliar_sem_mudar_o_esquema(banco50):
    a = analisar(banco50, [1], RAPIDO)
    con = sqlite3.connect(banco50)
    n_med = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
    con.close()
    an_id = analise.gravar_no_banco(banco50, a)
    an_id2 = analise.gravar_no_banco(banco50, a)                                       # idempotente (cria so se faltar)
    con = sqlite3.connect(banco50)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2 and an_id2 == an_id + 1
    assert con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0] == n_med
    crit_json, sha_c, res = con.execute("SELECT criterios_json, criterios_sha, resultado_json FROM analise_execucao WHERE an_id=?", (an_id,)).fetchone()
    con.close()
    assert json.loads(crit_json) == a.criterios.como_dict() and sha_c == a.criterios.sha()
    assert json.loads(res)["criterios"] == a.criterios.como_dict()
    analisar(banco50, [1], RAPIDO)                                                     # o esquema v2 continua abrindo normalmente
    Armazem(banco50).fechar()


# ----------------------------------------------------------------------------------------- formatacao e saidas
def test_formatacao_ptbr_virgula_decimal_e_milhar():
    f = relatorio
    assert f.fmt_num(5737) == "5.737" and f.fmt_num(1234567.891, 1) == "1.234.567,9" and f.fmt_num(0.5, 2) == "0,50"
    assert f.fmt_num(-1234.5, 1) == "-1.234,5" and f.fmt_num(999) == "999" and f.fmt_num(1000) == "1.000"
    assert f.fmt_num(12345.6789, 2, milhar=False) == "12345,68" and f.fmt_num(None) == "--" and f.fmt_num(float("nan")) == "--"
    assert f.fmt_sig(0.0005229) == "0,000523" and f.fmt_sig(0.5) == "0,500" and f.fmt_sig(12.5) == "12,5"
    assert f.fmt_sig(1.0) == "1,00" and f.fmt_sig(130.4) == "130" and f.fmt_sig(1234.5) == "1.234"
    assert f.fmt_auto(5737.0) == "5.737" and f.fmt_auto(13.0) == "13" and f.fmt_auto(13075.5) == "13.075,5" and f.fmt_auto(0.5) == "0,500"
    assert f.fmt_p(0.0001) == "<0,001" and f.fmt_p(0.03125) == "0,031" and f.fmt_p(1.0) == "1,000"
    assert f.fmt_pct(99.9477) == "99,9%" and f.fmt_pct(None) == "--"
    assert f.fmt_ic(0.48, 0.52) == "[0,480; 0,520]" and f.fmt_ic(None, 1.0) == "--"
    assert f.tx("-5,0%") == "$-$5,0\\%" and f.tx("<0,001") == "$<$0,001" and f.tx("[-1,0; 2,0]") == "[$-$1,0; 2,0]" and f.tx("--") == "--"
    assert f.esc("Q_01 & 50%") == "Q\\_01 \\& 50\\%"


def test_tabelas_latex_booktabs_e_ptbr(banco50, tmp_path):
    a = analisar(banco50, [1], RAPIDO)
    arquivos = relatorio.escrever_tabelas(a, tmp_path / "tab")
    assert {p.name for p in arquivos} == {"tab_resumo_consulta_quente.tex", "tab_tempo_consulta_quente.tex",
                                          "tab_efeito_antipadrao_quente.tex", "tab_sensibilidade_quente.tex"}
    for p in arquivos:
        t = p.read_text(encoding="utf-8")
        assert all(m in t for m in ("\\toprule", "\\midrule", "\\bottomrule", "\\begin{tabular}", "\\end{tabular}"))
        assert t.count("\\begin{tabular}") == t.count("\\end{tabular}") == 1 and t.count("{") == t.count("}")
        corpo = "\n".join(l for l in t.splitlines() if not l.startswith("%")).replace("0.9\\linewidth", "")
        assert not re.search(r"\d\.\d{1,2}(?!\d)|\d\.\d{4,}", corpo), "ponto decimal em tabela pt-BR"
        assert t.count("&") % 1 == 0 and "nan" not in corpo and "None" not in corpo
    resumo = (tmp_path / "tab" / "tab_resumo_consulta_quente.tex").read_text(encoding="utf-8")
    assert "8.000" in resumo and "4.000" in resumo and "0,500" in resumo and "50,0\\%" in resumo    # Q08: 8000 -> 4000, razao 0,5
    assert "\\usepackage{booktabs}" in resumo                                                    # aviso de dependencia no cabecalho
    efeito = (tmp_path / "tab" / "tab_efeito_antipadrao_quente.tex").read_text(encoding="utf-8")
    assert "0,008" in efeito and "0,023" in efeito and "Leituras lógicas" in efeito             # p = 2/256 e Holm = 0,0234
    assert "\\dagger" not in efeito                                                             # n = 8: nao e exploratorio


def test_csv_longo_e_criterios_gravados_junto_da_saida(banco50, tmp_path):
    a = analisar(banco50, [1], RAPIDO)
    arquivos = relatorio.escrever_csv(a, tmp_path / "o") + relatorio.escrever_json(a, tmp_path / "o")
    nomes = {p.name for p in arquivos}
    assert {"summary.csv", "comparisons.csv", "aggregate.csv", "verdicts.csv", "determinism.csv", "outliers.csv" if a.atipicos else "queries.csv",
            "analise.json", "criterios.json"} <= nomes
    resumo = list(csv.DictReader((tmp_path / "o" / "summary.csv").open(encoding="utf-8")))
    assert {"query_id", "variante", "regime", "metrica", "n", "minimo", "q1", "mediana", "q3", "maximo", "media", "dp", "cv"} <= set(resumo[0])
    assert len(resumo) == len(a.resumos) and resumo[0]["mediana"] and "," not in resumo[0]["mediana"]   # formato de maquina
    crit = json.loads((tmp_path / "o" / "criterios.json").read_text(encoding="utf-8"))
    assert crit["criterios"] == a.criterios.como_dict() and crit["criterios_sha"] == a.criterios.sha()
    assert crit["banco_sha256"] == sha(banco50) and crit["experimentos"] == [1]
    comp = list(csv.DictReader((tmp_path / "o" / "comparisons.csv").open(encoding="utf-8")))
    assert {c["metrica"] for c in comp} >= {"logical_reads", "cpu_ms", "elapsed_ms"} and all(c["run_ids_antes"] and c["run_ids_depois"] for c in comp)


def test_numeros_do_artigo_rastreaveis_a_run_id_e_experiment_id(banco50, tmp_path):
    a = analisar(banco50, [1], RAPIDO)
    relatorio.escrever_numeros(a, tmp_path / "n")
    dados = json.loads((tmp_path / "n" / "numeros-do-artigo.json").read_text(encoding="utf-8"))
    itens = dados["numeros"]
    ids = [i["id"] for i in itens]
    assert len(ids) == len(set(ids)) and dados["criterios_sha"] == a.criterios.sha() and dados["banco_sha256"] == sha(banco50)
    con = sqlite3.connect(banco50)
    runs_validos = {r for (r,) in con.execute("SELECT run_id FROM run")}
    con.close()
    for i in itens:
        assert i["experiment_ids"] == [1] and i["run_ids"] and set(i["run_ids"]) <= runs_validos and i["fonte"].endswith(".csv")
    por_id = {i["id"]: i for i in itens}
    razao = por_id["c:Q03:index:quente:logical_reads:razao_reg"]
    assert razao["valor"] == 0.5 and razao["texto"] == "0,500"
    assert por_id["c:Q08:index:quente:logical_reads:mediana_antes"]["texto"] == "8.000"
    assert por_id["a:principal:quente:index:todas=(todas):logical_reads:p_holm"]["texto"] == "0,023"
    assert por_id["d:Q01:antes:quente:logical_reads:dp"]["texto"] == "0"
    md = (tmp_path / "n" / "numeros-do-artigo.md").read_text(encoding="utf-8")
    assert "c:Q03:index:quente:logical_reads:razao_reg" in md and "run_id" in md


def test_figuras_pdf_vetoriais_e_reprodutiveis(banco50, tmp_path):
    pytest.importorskip("matplotlib")
    a = analisar(banco50, [1], RAPIDO)
    p1 = relatorio.escrever_figuras(a, tmp_path / "f1")
    p2 = relatorio.escrever_figuras(a, tmp_path / "f2")
    assert {p.name for p in p1} == {"razao_por_consulta_quente.pdf", "leituras_antes_depois_quente.pdf",
                                    "repeticoes_cpu_quente.pdf", "repeticoes_decorrido_quente.pdf"}
    for x, y in zip(sorted(p1), sorted(p2)):
        b = x.read_bytes()
        assert b.startswith(b"%PDF") and b"/Subtype /Image" not in b and len(b) > 3000        # vetorial, sem imagem raster
        assert b == y.read_bytes(), f"{x.name}: PDF nao reprodutivel"                          # sem data nem aleatoriedade


# ----------------------------------------------------------------------------------------- CLI
def test_cli_estatistica_e_relatorio_sem_carregar_config_do_sql_server(banco50, tmp_path, monkeypatch, capsys):
    from tcclab import cli

    def proibido(*a, **k):
        raise AssertionError("Config.carregar (segredo do SQL Server) nao deve ser lida por estatistica/relatorio")
    monkeypatch.setattr(cli.Config, "carregar", classmethod(lambda cls, **k: proibido()))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: proibido())
    crit = tmp_path / "crit.json"
    crit.write_text(json.dumps({"reducao_minima_pct": 30, "reamostragens": 500}))
    assert main(["estatistica", "--banco", str(banco50), "--experimento", "1", "--saida", str(tmp_path / "e"),
                 "--criterios", str(crit), "--semente", "99"]) == 0
    saida = capsys.readouterr().out
    assert "Q08" in saida and "SIM" in saida and "p Holm" in saida
    crits = json.loads((tmp_path / "e" / "criterios.json").read_text(encoding="utf-8"))["criterios"]
    assert crits["reducao_minima_pct"] == 30 and crits["semente"] == 99 and crits["reamostragens"] == 500   # arquivo + flag
    assert (tmp_path / "e" / "aggregate.csv").exists() and not (tmp_path / "e" / "tabelas").exists()
    assert main(["relatorio", "--banco", str(banco50), "--experimento", "1", "--saida", str(tmp_path / "r"),
                 "--reamostragens", "300", "--sem-figuras"]) == 0
    assert (tmp_path / "r" / "tabelas" / "tab_resumo_consulta_quente.tex").exists() and (tmp_path / "r" / "numeros-do-artigo.json").exists()
    assert not (tmp_path / "r" / "figuras").exists()
    with pytest.raises(SystemExit, match="inexistente"):
        main(["estatistica", "--banco", str(banco50), "--experimento", "5", "--saida", str(tmp_path / "x")])
    arq_ruim = tmp_path / "ruim.json"
    arq_ruim.write_text('{"nao_existe": 1}')
    with pytest.raises(SystemExit, match="desconhecido"):
        main(["estatistica", "--banco", str(banco50), "--experimento", "1", "--criterios", str(arq_ruim), "--saida", str(tmp_path / "x")])


def test_cli_antipadroes_e_gravar(banco50, tmp_path, capsys):
    ap = tmp_path / "ap.json"
    ap.write_text(json.dumps({f"Q{k:02d}": ("SARG" if k % 2 else "LIKE") for k in range(1, 9)}))
    assert main(["estatistica", "--banco", str(banco50), "--experimento", "1", "--saida", str(tmp_path / "e"), "--reamostragens", "200",
                 "--antipadroes", str(ap), "--gravar", "--json"]) == 0
    dados = json.loads(capsys.readouterr().out)
    assert {r["estrato"] for r in dados["agregados"] if r["estrato_tipo"] == "antipadrao"} == {"SARG", "LIKE"}
    con = sqlite3.connect(banco50)
    assert con.execute("SELECT COUNT(*) FROM analise_execucao").fetchone()[0] == 1
    con.close()


def test_subcomandos_antigos_continuam_no_parser():
    from tcclab.cli import construir_parser
    p = construir_parser()
    assert p.parse_args(["ping"]).cmd == "ping"
    assert p.parse_args(["exportar", "--experimento", "1"]).formato == "csv"
    assert p.parse_args(["medir", "--sql", "SELECT 1"]).repeticoes == 10
    a = p.parse_args(["relatorio", "--experimento", "1", "2", "3"])
    assert a.experimento == [1, 2, 3] and a.banco_sqlite == "resultados/tcclab.sqlite" and a.saida == "resultados/analise"
    assert p.parse_args(["estatistica"]).experimento is None                                   # padrao: todos do arquivo


def test_cli_sem_experimento_usa_todos_e_exclui_os_descartados(tmp_path, capsys):
    cons = [consulta_completa(0)]
    arm, e1 = construir_variantes(tmp_path / "c.sqlite", cons, experimento="t1")
    _, e2 = construir_variantes(tmp_path / "c.sqlite", cons, experimento="t2", arm=arm)
    arm.fechar()
    coletor(tmp_path / "c.sqlite", {"Q01": e2}, descartados=())
    con = sqlite3.connect(tmp_path / "c.sqlite")
    con.execute("INSERT INTO coleta_consulta(rodada, query_id, experiment_id, tentativa, status, deriva_a1_a2, descartado_por_deriva) "
                "VALUES ('r', 'Q01', ?, 1, 'concluido', 0.3, 1)", (e1,))
    con.commit()
    con.close()
    assert main(["estatistica", "--banco", str(tmp_path / "c.sqlite"), "--saida", str(tmp_path / "o"), "--reamostragens", "200"]) == 0
    out = capsys.readouterr().out
    assert "DESCARTADO (deriva)" in out and f"experimentos [{e2}]" in out
    assert json.loads((tmp_path / "o" / "criterios.json").read_text(encoding="utf-8"))["experimentos"] == [e2]


# ----------------------------------------------------------------------------------------- pre-registro: papeis, escrita sem limiar, deriva
def consulta_controle(i):
    """Sem efeito algum (razao 1): como os controles F5/S5 do pre-registro."""
    c = consulta_fixa(i, fator=1.0)
    c["id"] = f"C{i + 1}"
    return c


def test_controles_ficam_fora_da_analise_principal_e_em_tabela_propria(tmp_path):
    cons = [consulta_fixa(i) for i in range(8)] + [consulta_controle(0), consulta_controle(1)]
    arm, exp, _ = construir(tmp_path / "pap.sqlite", cons)
    arm.db.execute("UPDATE catalog_query SET notes = 'controle' WHERE query_id IN ('C1', 'C2')")   # como experimento/coletar.py
    arm.db.execute("UPDATE catalog_query SET notes = 'texto livre qualquer' WHERE query_id = 'Q01'")  # nao e papel: vira principal
    arm.db.commit()
    arm.fechar()
    a = analisar(tmp_path / "pap.sqlite", [exp], RAPIDO)
    lr = lambda an: linhas(a, "agregados", analise=an, estrato_tipo="todas", metrica="logical_reads")[0]   # noqa: E731
    assert lr("principal")["n_consultas"] == 8 and lr("principal")["reducao_mediana_pct"] == 50.0
    assert lr("sem_atipicos")["n_consultas"] == 8
    assert lr("com_controles")["n_consultas"] == 10 and lr("controles")["n_consultas"] == 2
    assert lr("controles")["reducao_mediana_pct"] == 0.0 and lr("controles")["n_melhoria_significativa"] == 0
    assert lr("principal")["n_melhoria_significativa"] == 8 and lr("com_controles")["n_melhoria_significativa"] == 8
    assert {v["query_id"]: v["papel"] for v in a.veredictos}["C1"] == "controle"
    assert any("controles" in av and "C1" in av for av in a.avisos)
    # tabela principal sem os controles; tabela propria com eles
    relatorio.escrever_tabelas(a, tmp_path / "tab")
    principal = (tmp_path / "tab" / "tab_resumo_consulta_quente.tex").read_text(encoding="utf-8")
    controles = (tmp_path / "tab" / "tab_controles_quente.tex").read_text(encoding="utf-8")
    assert "C1" not in principal and "Q08" in principal and "C1" in controles and "Q08" not in controles
    assert "controle" in controles and "fora da análise principal" in controles
    ag = (tmp_path / "tab" / "tab_efeito_antipadrao_quente.tex").read_text(encoding="utf-8")
    assert "$n$" in ag and re.search(r"Todas & 8 &", ag)                                         # o agregado das tabelas e o principal
    # o argumento prevalece sobre o catalogo; papel invalido e erro
    b = analisar(tmp_path / "pap.sqlite", [exp], RAPIDO, papeis={"Q01": "controle", "C1": "principal"})
    assert linhas(b, "agregados", analise="principal", estrato_tipo="todas", metrica="logical_reads")[0]["n_consultas"] == 8
    assert linhas(b, "agregados", analise="controles", estrato_tipo="todas", metrica="logical_reads")[0]["n_consultas"] == 2
    with pytest.raises(ValueError, match="papel invalido"):
        analisar(tmp_path / "pap.sqlite", [exp], RAPIDO, papeis={"Q01": "estranho"})
    # sem nenhum controle nao ha conjuntos extras nem tabela de controles
    arm2, exp2, _ = construir(tmp_path / "pap2.sqlite", [consulta_fixa(i) for i in range(3)])
    arm2.fechar()
    c = analisar(tmp_path / "pap2.sqlite", [exp2], RAPIDO)
    assert {r["analise"] for r in c.agregados} == {"principal", "sem_atipicos"}
    assert not [p for p in relatorio.escrever_tabelas(c, tmp_path / "tab2") if "controles" in p.name]


def test_piora_e_deriva_com_limite_do_pre_registro(tmp_path):
    assert Criterios().limite_deriva_pct == 25.0
    # baseline: A1 com mediana 10 e A2 com mediana 12 (+20%) em CPU; tratamento piora as leituras (razao 1,5)
    cons = [dict(id="Q1", antes=dict(logical_reads=serie(100, 20), cpu_ms=[10] * 10 + [12] * 10, elapsed_ms=serie(10, 20)),
                 depois=dict(logical_reads=serie(150, 10), cpu_ms=serie(5, 10), elapsed_ms=serie(5, 10)))]
    arm, exp, _ = construir(tmp_path / "dv.sqlite", cons)
    arm.fechar()
    a = analisar(tmp_path / "dv.sqlite", [exp], RAPIDO)
    v = a.veredictos[0]
    assert v["piora"] is True and v["melhoria_significativa"] is False and v["reducao_pct"] == -50.0
    c = linhas(a, "comparacoes", metrica="cpu_ms")[0]
    assert c["deriva_base_pct"] == pytest.approx(20.0) and c["deriva_alerta"] is False          # 20% < 25%
    b = analisar(tmp_path / "dv.sqlite", [exp], Criterios(reamostragens=1000, limite_deriva_pct=10.0))
    assert linhas(b, "comparacoes", metrica="cpu_ms")[0]["deriva_alerta"] is True
    assert linhas(a, "comparacoes", metrica="logical_reads")[0]["deriva_base_pct"] == 0.0


# ----------------------------------------------------------------------------------------- PRE-REGISTRO: papeis das variantes, descartes, regimes
def test_prescricao_e_o_tratamento_principal_e_os_outros_bracos_ficam_fora_do_agregado(tmp_path):
    cons = [consulta_completa(i, "AP1") for i in range(6)] + [consulta_completa(6, "AP4", com_reescrita=False)]
    cons[0]["variantes"].append(var("contraexemplo: reescrita + indice nao cobrindo", "contraexemplo", 900))
    cons[1]["variantes"].append(var("controle: ordem invertida das colunas", "controle_ordem", 900))
    arm, exp = construir_variantes(tmp_path / "pr.sqlite", cons)
    arm.fechar()
    a = analisar(tmp_path / "pr.sqlite", [exp], RAPIDO)
    ll = {(c["query_id"], c["tratamento_kind"], c["comparacao"]): c for c in a.comparacoes if c["metrica"] == "logical_reads"}
    assert ll[("Q01", "prescricao", "principal")]["razao"] == pytest.approx(0.1) and ll[("Q01", "prescricao", "principal")]["grupo"] == analise.PRESCRICAO
    assert ll[("Q07", "indice", "principal")]["razao"] == pytest.approx(0.25) and ll[("Q07", "indice", "principal")]["grupo"] == analise.PRESCRICAO   # AP4
    assert ll[("Q01", "indice", "secundaria")]["razao"] == pytest.approx(0.4) and ll[("Q01", "reescrita", "secundaria")]["razao"] == pytest.approx(0.7)
    assert ll[("Q01", "nativo", "secundaria")]["razao"] == pytest.approx(0.08)                 # nativo contra a original...
    nv = ll[("Q01", "nativo", "nativo_vs_prescricao")]                                          # ...e contra a prescricao
    assert nv["razao"] == pytest.approx(0.8) and nv["baseline"] == "reescrita+indice" and nv["mediana_antes"] == 100.0
    assert ll[("Q01", "contraexemplo", "secundaria")]["razao"] == pytest.approx(0.9)
    assert ll[("Q02", "controle_ordem", "secundaria")]["razao"] == pytest.approx(0.45)
    assert not [k for k in ll if k[0] == "Q07" and k[1] != "indice"]                            # AP4 so tem original e indice
    # agregado principal: 7 consultas (6 com reescrita + AP4), todas com a razao do tratamento principal
    ag = {(r["analise"], r["grupo"], r["estrato"]): r for r in a.agregados if r["metrica"] == "logical_reads" and r["regime"] == "quente"}
    pr = ag[("principal", analise.PRESCRICAO, "(todas)")]
    assert pr["n_consultas"] == 7 and pr["n_melhoria_significativa"] == 7
    assert pr["reducao_mediana_pct"] == pytest.approx(90.0)                                     # mediana das razoes = 0,1 (6 de 7 em 0,1)
    assert {k[1] for k in ag if k[0] == "principal"} == {analise.PRESCRICAO}                    # nenhum braco secundario no agregado principal
    assert ag[("principal", analise.PRESCRICAO, "AP4")]["n_consultas"] == 1 and ag[("principal", analise.PRESCRICAO, "AP1")]["n_consultas"] == 6
    sec = {k[1]: r for k, r in ag.items() if k[0] == "secundarias" and k[2] == "(todas)"}
    assert set(sec) == {"reescrita", "indice", "nativo", "nativo_vs_prescricao", "contraexemplo", "controle_ordem"}
    assert sec["reescrita"]["n_consultas"] == 6 and sec["indice"]["n_consultas"] == 6          # o indice do AP4 e o principal dele
    assert sec["contraexemplo"]["n_consultas"] == 1 and sec["nativo_vs_prescricao"]["n_consultas"] == 6
    assert sec["reescrita"]["reducao_mediana_pct"] == pytest.approx(30.0)
    assert sec["nativo_vs_prescricao"]["n_melhoria_significativa"] is None                      # sem veredito (nao e contra a original)
    # veredictos: principal e secundarios contra a original; nativo_vs_prescricao nao tem veredito
    assert {v["comparacao"] for v in a.veredictos} == {"principal", "secundaria"}
    assert len([v for v in a.veredictos if v["comparacao"] == "principal"]) == 7
    # tabelas: a principal so tem o tratamento principal; os bracos vao para a tabela propria
    relatorio.escrever_tabelas(a, tmp_path / "tab")
    principal = (tmp_path / "tab" / "tab_resumo_consulta_quente.tex").read_text(encoding="utf-8")
    secund = (tmp_path / "tab" / "tab_secundarias_quente.tex").read_text(encoding="utf-8")
    assert principal.count("\\\\") == 1 + 7 and "contraexemplo" not in principal and "nativo" not in principal
    assert "contraexemplo" in secund and "nativo" in secund and "prescrição" in secund and "controle: ordem" in secund
    json.loads(analise.para_json(a))


def test_experimentos_descartados_por_deriva_sao_excluidos_e_listados(tmp_path):
    cons = [consulta_completa(0)]
    arm, e1 = construir_variantes(tmp_path / "d.sqlite", cons, experimento="t1")
    _, e2 = construir_variantes(tmp_path / "d.sqlite", cons, experimento="t2", arm=arm)
    arm.fechar()
    con = sqlite3.connect(tmp_path / "d.sqlite")
    con.executescript(DDL_COLETOR)
    con.execute("INSERT INTO coleta_consulta(rodada, query_id, experiment_id, tentativa, status, deriva_a1_a2, descartado_por_deriva) "
                "VALUES ('r', 'Q01', ?, 1, 'concluido', 0.256, 1)", (e1,))
    con.execute("INSERT INTO coleta_consulta(rodada, query_id, experiment_id, tentativa, status, deriva_a1_a2, descartado_por_deriva) "
                "VALUES ('r', 'Q01', ?, 2, 'concluido', 0.194, 0)", (e2,))
    con.commit()
    con.close()
    for pedido in (None, [e1, e2]):                                    # todos do arquivo, ou os dois pedidos explicitamente
        a = analisar(tmp_path / "d.sqlite", pedido, RAPIDO)
        assert a.meta["experimentos"] == [e2] and a.meta["descartados_ids"] == [e1]
        assert a.descartados == [dict(rodada="r", query_id="Q01", experiment_id=e1, tentativa=1, deriva_a1_a2=0.256)]
        assert [c["consulta"] for c in a.consultas] == ["Q01"]       # sem o sufixo "(exp. N)": a tentativa 1 nao entra
        assert {r["experiment_id"] for r in a.comparacoes} == {e2}
        assert any("EXCLUIDO" in av and str(e1) in av for av in a.avisos) and not any("query_id repetido" in av for av in a.avisos)
    relatorio.escrever_csv(a, tmp_path / "o")
    lido = list(csv.DictReader((tmp_path / "o" / "descartados.csv").open(encoding="utf-8")))
    assert len(lido) == 1 and lido[0]["experiment_id"] == str(e1) and "ATENCAO" not in relatorio.relatorio_texto(a)
    assert "DESCARTADO" in relatorio.relatorio_texto(a)
    with pytest.raises(ValueError, match="descartados por deriva"):
        analisar(tmp_path / "d.sqlite", [e1], RAPIDO)
    # sem as tabelas do coletor (banco do M0) nada e excluido
    arm2, e3 = construir_variantes(tmp_path / "d2.sqlite", cons)
    arm2.fechar()
    assert analisar(tmp_path / "d2.sqlite", None, RAPIDO).descartados == []


def test_leituras_logicas_diferentes_entre_regimes_usam_o_quente_como_principal(tmp_path):
    # key lookup: o frio le mais paginas (pre-carregamento assincrono tambem conta): 46.157 quente x 78.050 frio
    q = dict(id="C1", antipadrao="AP3", variantes=[
        ("original", "original", {"quente": dict(logical_reads=serie(100000, 40)), "frio": dict(logical_reads=serie(100000, 16))}),
        ("reescrita+indice", "prescricao", {"quente": dict(logical_reads=serie(46157, 20)), "frio": dict(logical_reads=serie(78050, 8))})])
    arm, exp = construir_variantes(tmp_path / "rg.sqlite", [q], regimes=("quente", "frio"))
    arm.fechar()
    a = analisar(tmp_path / "rg.sqlite", [exp], RAPIDO)
    d = {(r["variante"], r["regime"]): r for r in a.determinismo}
    assert d[("reescrita+indice", "quente")]["difere_entre_regimes"] is True and d[("original", "quente")]["difere_entre_regimes"] is False
    assert d[("reescrita+indice", "frio")]["medianas_por_regime"] == "frio=78050;quente=46157"
    assert any("DIFEREM entre regimes" in av and "'quente'" in av for av in a.avisos)
    v = {r["regime"]: r for r in a.veredictos}
    assert v["quente"]["reducao_pct"] == pytest.approx((1 - 46157 / 100000) * 100) and v["frio"]["reducao_pct"] == pytest.approx((1 - 78050 / 100000) * 100)
    # cada regime tem suas tabelas; os destaques de numeros-do-artigo sao do regime quente
    arqs = {p.name for p in relatorio.escrever_tabelas(a, tmp_path / "t")}
    assert {"tab_resumo_consulta_quente.tex", "tab_resumo_consulta_frio.tex"} <= arqs
    relatorio.escrever_numeros(a, tmp_path / "n")
    itens = {i["id"]: i for i in json.loads((tmp_path / "n" / "numeros-do-artigo.json").read_text(encoding="utf-8"))["numeros"]}
    assert itens["c:C1:prescricao:quente:logical_reads:mediana_depois"]["destaque"] is True
    assert itens["c:C1:prescricao:frio:logical_reads:mediana_depois"]["destaque"] is False
    assert itens["c:C1:prescricao:frio:logical_reads:mediana_depois"]["texto"] == "78.050"


def test_escrita_no_texto_e_nas_tabelas_e_numeros_com_origem(tmp_path):
    arm, exp = construir_variantes(tmp_path / "e.sqlite", [consulta_completa(0)])
    arm.fechar()
    coletor(tmp_path / "e.sqlite", {"Q01": exp}, escrita={"Q01": {"insert": (7800, 11000, 1865172, 1994492), "update": (217, 12470, 120120, 364120),
                                                                  "delete": (217, 10137, 1564652, 1505700)}})
    a = analisar(tmp_path / "e.sqlite", [exp], RAPIDO)
    relatorio.escrever_tabelas(a, tmp_path / "t")
    t = (tmp_path / "t" / "tab_escrita.tex").read_text(encoding="utf-8")
    assert "INSERT" in t and "7.800" in t and "11.000" in t and "1,41" in t and "57,5" in t and "Sem limiar" in t
    relatorio.escrever_numeros(a, tmp_path / "n")
    itens = {i["id"]: i for i in json.loads((tmp_path / "n" / "numeros-do-artigo.json").read_text(encoding="utf-8"))["numeros"]}
    e = itens["e:Q01:update:logical_reads:razao"]
    assert e["texto"] == "57,5" and e["experiment_ids"] == [exp] and "escrita_medicao (rodada r, query_id Q01)" in e["origem_sqlite"]
    assert "ESCRITA" not in relatorio.relatorio_texto(a) and "custo de escrita" in relatorio.relatorio_texto(a)


PILOTO2 = Path(__file__).resolve().parents[2] / "experimento" / "resultados" / "piloto-2" / "tcclab.sqlite"


@pytest.mark.skipif(not PILOTO2.exists(), reason="piloto-2 do coletor ausente")
def test_piloto2_dados_reais_do_coletor(tmp_path, monkeypatch):
    """Dados reais (so leitura, em copia): 4 experimentos, o 1o descartado por deriva; C1 (AP3), S1 (AP2), F1 (AP1)."""
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("rede")))
    copia = tmp_path / "piloto2.sqlite"
    sha_original = sha(PILOTO2)
    shutil.copy(PILOTO2, copia)
    a = analisar(copia, None, RAPIDO)
    assert a.meta["experimentos"] == [2, 3, 4] and a.meta["descartados_ids"] == [1]
    assert [(d["query_id"], d["tentativa"]) for d in a.descartados] == [("C1", 1)]
    assert {c["consulta"] for c in a.consultas} == {"C1", "S1", "F1"}                     # sem "(exp. N)": a tentativa 1 saiu
    assert {c["query_id"]: c["antipadrao"] for c in a.consultas} == {"C1": "AP3", "S1": "AP2", "F1": "AP1"}
    assert {c["papel"] for c in a.consultas} == {"principal"}
    quente = {v["query_id"]: v for v in a.veredictos if v["regime"] == "quente" and v["comparacao"] == "principal"}
    assert {k: (v["mediana_antes"], v["mediana_depois"]) for k, v in quente.items()} == {
        "C1": (800856.0, 41.0), "S1": (2409973.0, 24239.0), "F1": (800856.0, 47.0)}
    assert all(v["tratamento_kind"] == "prescricao" and v["melhoria_significativa"] and v["escrita_razao"] > 1 for v in quente.values())
    assert all(v["criterio_escrita"] == "sem_limiar" for v in quente.values())            # PRE-REGISTRO: razao de escrita, sem limiar
    sec = {(c["query_id"], c["tratamento_kind"], c["comparacao"]): c for c in a.comparacoes
           if c["regime"] == "quente" and c["metrica"] == "logical_reads" and c["comparacao"] != "principal"}
    assert sec[("S1", "contraexemplo", "secundaria")]["razao"] > 2 and sec[("C1", "reescrita", "secundaria")]["razao"] == 1.0
    assert ("C1", "nativo", "nativo_vs_prescricao") in sec and sec[("C1", "nativo", "nativo_vs_prescricao")]["baseline"] == "reescrita+indice"
    assert {v["query_id"] for v in a.veredictos if v["tratamento_kind"] == "contraexemplo" and v["regime"] == "quente"} == {"S1"}
    assert [v["piora"] for v in a.veredictos if v["tratamento_kind"] == "contraexemplo" and v["regime"] == "quente"] == [True]
    # agregado principal: so as 3 consultas do tratamento principal, em um unico grupo
    pr = [r for r in a.agregados if r["analise"] == "principal" and r["regime"] == "quente" and r["estrato"] == "(todas)"]
    assert {r["grupo"] for r in pr} == {analise.PRESCRICAO} and all(r["n_consultas"] == 3 for r in pr)
    assert all(r["poder_insuficiente"] for r in pr) and any("n_min_consultas" in av for av in a.avisos)
    # leituras logicas: C1 com indice so na chave (key lookup) le mais no frio que no quente
    d = {(r["query_id"], r["variante"], r["regime"]): r for r in a.determinismo}
    assert d[("C1", "indice", "quente")]["mediana"] == 46157.0 and d[("C1", "indice", "frio")]["mediana"] > 78000
    assert d[("C1", "indice", "quente")]["difere_entre_regimes"] is True and any("DIFEREM entre regimes" in av for av in a.avisos)
    # custo de escrita: 3 consultas x 3 operacoes x 4 metricas, razao com/sem indice (so reportada)
    assert len(a.escrita) == 36
    up = {r["query_id"]: r for r in a.escrita if r["operacao"] == "update" and r["metrica"] == "logical_reads"}
    assert {k: r["mediana_sem"] for k, r in up.items()} == {"C1": 217.0, "S1": 217.0, "F1": 217.0} and all(r["razao"] > 50 for r in up.values())
    saida = relatorio.gerar_relatorio(a, tmp_path / "out", figuras=False)
    nomes = {p.name for p in saida}
    assert {"descartados.csv", "escrita.csv", "tab_escrita.tex", "tab_secundarias_quente.tex", "tab_secundarias_frio.tex",
            "tab_resumo_consulta_quente.tex", "tab_resumo_consulta_frio.tex", "numeros-do-artigo.json"} <= nomes
    assert sha(PILOTO2) == sha_original and not (PILOTO2.parent / "analise").exists()     # o original nao foi tocado nem recebeu saida


# ----------------------------------------------------------------------------------------- dados reais do M0 (copia)
EXEMPLO = Path(__file__).resolve().parents[1] / "resultados" / "tcclab.sqlite"


@pytest.mark.skipif(not EXEMPLO.exists(), reason="experimento de exemplo ausente")
def test_experimento_de_exemplo_do_m0_5737_para_3(tmp_path):
    copia = tmp_path / "exemplo.sqlite"
    shutil.copy(EXEMPLO, copia)                                                     # nunca analisa o original
    con = sqlite3.connect(copia)
    nome = con.execute("SELECT name FROM experiment WHERE experiment_id=1").fetchone()
    con.close()
    if not nome or nome[0] != "caso13_30reps":
        pytest.skip("experimento 1 nao e o caso13_30reps")
    a = analisar(copia, [1], RAPIDO)
    q = linhas(a, "veredictos", regime="quente")[0]
    assert (q["mediana_antes"], q["mediana_depois"]) == (5737.0, 3.0) and q["razao"] == pytest.approx(3 / 5737)
    assert q["melhoria_significativa"] and q["criterio_escrita"] == "sem_dados"
    assert all(d["deterministico"] for d in a.determinismo)                         # dp = 0 nas leituras logicas
    cpu = linhas(a, "comparacoes", regime="quente", metrica="cpu_ms")[0]
    assert cpu["mediana_depois"] == 0 and cpu["regra_zero"] == "depois_zero" and cpu["reducao_pct"] == 100.0
    assert any("n_min_consultas" in av for av in a.avisos)                          # 1 consulta: exploratorio, dito
