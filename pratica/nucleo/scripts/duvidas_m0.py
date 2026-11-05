"""Experimentos do M0 que respondem as duvidas (a) a (d) do relatorio 19. Imprime JSON e grava em
resultados/duvidas_m0.json. Cria e remove tabelas m0_* em tcc_lab. Exige o container rodando.
Uso: .venv/bin/python scripts/duvidas_m0.py [--grande]   (--grande cria m0_grande com 1 M de linhas)"""
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

from tcclab import canais
from tcclab.config import Config
from tcclab.driver import conectar
from tcclab.mensagens import analisar
from tcclab.medir import medir_uma, preparar_sessao
from tcclab.cache import limpar_cache_planos, preparar_frio
from tcclab.stats import resumir

cfg = Config.carregar()
con = conectar(cfg)
preparar_sessao(con)
SQL = "SELECT COUNT(*), SUM(valor) FROM dbo.m0_sanidade WHERE cliente = 42"
OUT: dict = {}


def docker(cmd: str) -> str:
    return subprocess.run(["docker", "exec", "tcc-sqlserver", "bash", "-c", cmd], capture_output=True, text=True).stdout


def vmstat_container() -> dict:
    d = {}
    for l in docker("cat /proc/vmstat").splitlines():
        k, v = l.split()
        d[k] = int(v)
    cached = int(re.search(r"Cached:\s+(\d+)", docker("cat /proc/meminfo")).group(1))
    return {"pgpgin_kb": d.get("pgpgin"), "pgpgout_kb": d.get("pgpgout"), "cached_kb": cached}


def vmstat_mac() -> dict:
    t = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout
    g = lambda k: int(re.search(rf"{k}:\s+(\d+)", t).group(1))  # noqa: E731
    return {"pageins": g("Pageins"), "file_backed": g("File-backed pages")}


def um(sql):
    return con.executar_lote(sql, guardar_linhas=5).conjuntos[0].linhas[0]


# ------------------------------------------------------------------ (a) mensagens e idioma
r = con.executar_lote(SQL)
OUT["a_mensagens_us_english"] = [{"numero": m.numero, "texto": m.texto} for m in r.mensagens]
idiomas = con.executar_lote("SELECT name, alias FROM sys.syslanguages WHERE alias IN ('Brazilian','German','French')", guardar_linhas=10).conjuntos[0].linhas
OUT["a_idiomas_disponiveis"] = [list(x) for x in idiomas]
loc = {}
for alias in ("Brazilian", "German", "French"):
    con.executar_lote(f"SET LANGUAGE {alias}")
    r = con.executar_lote(SQL)
    loc[alias] = [{"numero": m.numero, "texto": m.texto} for m in r.mensagens]
    est = analisar(r.mensagens)
    loc[alias].append({"parse_ok_por_numero": [est.soma("logical_reads"), est.cpu_ms, est.elapsed_ms]})
con.executar_lote("SET LANGUAGE us_english")
OUT["a_localizacao"] = loc

# ------------------------------------------------------------------ (b) QueryPlanHash e plano real
limpar_cache_planos(con, cfg, "tcc_lab")
xml_real, pa = canais.capturar_plano_real(con, SQL)
ins = pa.principal()
xml_est, pe = canais.capturar_plano_estimado(con, SQL)
ie = pe.principal()
b = {"real": {"query_hash": ins.query_hash, "query_plan_hash": ins.query_plan_hash, "hash_formato": ins.hash_formato},
     "estimado": {"query_hash": ie.query_hash, "query_plan_hash": ie.query_plan_hash, "hash_formato": ie.hash_formato}}
dmv = canais.instantaneo_dmv(con, ins.query_hash)
b["dmv_query_plan_hash"] = list(dmv)
qs = canais.instantaneo_qs(con, ins.query_hash)
b["qs_query_plan_hash"] = [t.query_plan_hash for t in qs.values()]
hashes = []
for _ in range(3):  # recompilacoes independentes
    limpar_cache_planos(con, cfg, "tcc_lab")
    _, p = canais.capturar_plano_real(con, SQL)
    hashes.append(p.principal().query_plan_hash)
b["tres_recompilacoes"] = hashes
_, p43 = canais.capturar_plano_real(con, SQL.replace("42", "43"))
b["outro_valor_literal_43"] = {"query_hash": p43.principal().query_hash, "query_plan_hash": p43.principal().query_plan_hash}
_, pj = canais.capturar_plano_real(con, SQL.replace("cliente = 42", "cliente = 42 AND valor > 0"))
b["outra_consulta"] = {"query_hash": pj.principal().query_hash, "query_plan_hash": pj.principal().query_plan_hash}
raiz = xml_real
attrs = {}
for tag in ("StmtSimple", "QueryPlan", "RunTimeCountersPerThread", "RelOp", "QueryTimeStats", "MemoryGrantInfo", "OptimizerHardwareDependentProperties"):
    nomes = set()
    for m in re.finditer(rf"<{tag}\s([^>]*)>", raiz):
        nomes |= set(re.findall(r'(\w+)="', m.group(1)))
    attrs[tag] = sorted(nomes)
b["atributos_do_plano_real"] = attrs
b["elementos_do_plano_real"] = sorted(set(re.findall(r"<(\w+)[ >]", raiz)))
b["operadores"] = [vars(o) for o in ins.operadores]
b["leituras_logicas_plano"] = ins.leituras_logicas_plano
OUT["b_plano"] = b

# ------------------------------------------------------------------ (c) DROPCLEANBUFFERS
def bloco_c(tabela_sql, rotulo, n=10):
    limpar_cache_planos(con, cfg, "tcc_lab")
    medir_uma(con, tabela_sql)  # compila + aquece
    quente = [medir_uma(con, tabela_sql) for _ in range(n)]
    frio, extras = [], []
    for _ in range(n):
        preparar_frio(con, cfg, "tcc_lab")
        bd = um("SELECT COUNT(*) FROM sys.dm_os_buffer_descriptors WHERE database_id = DB_ID()")[0]
        v0, m0 = vmstat_container(), vmstat_mac()
        m = medir_uma(con, tabela_sql)
        v1, m1 = vmstat_container(), vmstat_mac()
        bd2 = um("SELECT COUNT(*) FROM sys.dm_os_buffer_descriptors WHERE database_id = DB_ID()")[0]
        frio.append(m)
        extras.append({"paginas_no_buffer_antes": bd, "paginas_no_buffer_depois": bd2,
                       "vm_pgpgin_kb": v1["pgpgin_kb"] - v0["pgpgin_kb"], "vm_cached_kb_delta": v1["cached_kb"] - v0["cached_kb"],
                       "mac_pageins": m1["pageins"] - m0["pageins"]})
    f = lambda L, k: resumir([float(getattr(x, k)) for x in L]).como_dict()  # noqa: E731
    return {"rotulo": rotulo,
            "quente": {k: f(quente, k) for k in ("logical_reads", "physical_reads", "read_ahead_reads", "cpu_ms", "elapsed_ms")},
            "frio": {k: f(frio, k) for k in ("logical_reads", "physical_reads", "read_ahead_reads", "cpu_ms", "elapsed_ms")},
            "frio_extras_por_execucao": extras}


OUT["c_pequena"] = bloco_c(SQL, "m0_sanidade (5737 paginas, ~45 MB)")
if "--grande" in sys.argv:
    con.executar_lote("IF OBJECT_ID('dbo.m0_grande') IS NOT NULL DROP TABLE dbo.m0_grande")
    con.executar_lote("""CREATE TABLE dbo.m0_grande(id INT NOT NULL PRIMARY KEY, cliente INT NOT NULL, valor DECIMAL(10,2) NOT NULL, pad CHAR(200) NOT NULL DEFAULT 'x')""")
    con.executar_lote("""INSERT dbo.m0_grande(id, cliente, valor) SELECT TOP (1000000) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)), ABS(CHECKSUM(NEWID())) % 5000, 10
                         FROM sys.all_objects a CROSS JOIN sys.all_objects b CROSS JOIN sys.all_objects c""")
    try:
        OUT["c_grande"] = bloco_c("SELECT COUNT(*), SUM(valor) FROM dbo.m0_grande WHERE cliente = 42", "m0_grande (1 M linhas)", n=5)
    finally:
        con.executar_lote("DROP TABLE dbo.m0_grande")

# ------------------------------------------------------------------ (d) resolucao
con.executar_lote("IF INDEXPROPERTY(OBJECT_ID('dbo.m0_sanidade'), 'ix_m0_res', 'IndexId') IS NOT NULL DROP INDEX ix_m0_res ON dbo.m0_sanidade")
con.executar_lote("CREATE INDEX ix_m0_res ON dbo.m0_sanidade(cliente) INCLUDE (valor)")
try:
    limpar_cache_planos(con, cfg, "tcc_lab")
    _, ps = canais.capturar_plano_real(con, SQL)
    qh, qph = ps.principal().query_hash, ps.principal().query_plan_hash
    linhas = []
    for _ in range(40):
        m = medir_uma(con, SQL)
        u = canais.ultima_execucao_dmv(con, qh, qph)
        linhas.append((m.cpu_ms, m.elapsed_ms, u["cpu_us"], u["elapsed_us"]))
    OUT["d_resolucao"] = {
        "plano_QueryTimeStats_ms": [ps.principal().cpu_ms, ps.principal().elapsed_ms],
        "statistics_time_cpu_ms_valores": dict(Counter(l[0] for l in linhas)),
        "statistics_time_elapsed_ms_valores": dict(Counter(l[1] for l in linhas)),
        "dmv_last_cpu_us": resumir([float(l[2]) for l in linhas]).como_dict(),
        "dmv_last_elapsed_us": resumir([float(l[3]) for l in linhas]).como_dict(),
        "dmv_cpu_us_valores_distintos": len({l[2] for l in linhas}),
        "dmv_cpu_us_primeiros": [l[2] for l in linhas[:10]],
        "dmv_cpu_us_multiplos_de_1000": sum(1 for l in linhas if l[2] % 1000 == 0),
    }
finally:
    con.executar_lote("DROP INDEX ix_m0_res ON dbo.m0_sanidade")
con.fechar()
caminho = Path(__file__).resolve().parents[1] / "resultados" / "duvidas_m0.json"
caminho.parent.mkdir(exist_ok=True)
caminho.write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
print(json.dumps(OUT, ensure_ascii=False, indent=1, default=str))
