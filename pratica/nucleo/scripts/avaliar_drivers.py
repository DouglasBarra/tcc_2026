"""Avalia python-tds, pymssql, mssql-python e pyodbc nas 4 capacidades exigidas pelo M0:
 1) conectar pelo host na porta 1433; 2) capturar as mensagens de STATISTICS IO/TIME;
 3) obter o plano real em XML; 4) rodar DBCC DROPCLEANBUFFERS.
Exige a tabela dbo.m0_sanidade (use tests/conftest.py ou crie manualmente). Uso:
  .venv/bin/pip install -r requirements-avaliacao.txt && .venv/bin/python scripts/avaliar_drivers.py"""
import re
import time
import traceback

from tcclab.config import Config

cfg = Config.carregar()
H, DB, PW = cfg.host, "tcc_lab", cfg.password
Q = "SELECT COUNT(*), SUM(valor) FROM dbo.m0_sanidade WHERE cliente = 42"
SETS = "SET STATISTICS IO ON; SET STATISTICS TIME ON; "


def resumo(nome, conectou, msgs, xml, dropclean, extra=""):
    ios = [m for m in msgs if re.search(r"logical reads (\d+)", m)]
    lr = re.search(r"logical reads (\d+)", ios[0]).group(1) if ios else None
    tm = [m for m in msgs if "Execution Times" in m]
    print(f"{nome:13} conexao={conectou!s:5} mensagens={len(msgs)} leituras_logicas={lr} tempo_exec={'sim' if tm else 'nao'} "
          f"plano_xml={xml!s:5} dropcleanbuffers={dropclean!s:5} {extra}")


def pytds_():
    import pytds
    c = pytds.connect(H, database=DB, user="sa", password=PW, port=cfg.port, autocommit=True)
    cur = c.cursor()
    cur.execute(SETS + Q); cur.fetchall(); cur.nextset()
    msgs = [f"[{m[1].number}] {m[1].text}" for m in cur.messages]
    cur.execute("SET STATISTICS XML ON; " + Q)
    xml = False
    while True:
        try:
            xml = xml or any("<ShowPlanXML" in str(r[0]) for r in cur.fetchall())
        except Exception:
            pass
        if not cur.nextset():
            break
    cur.execute("CHECKPOINT; DBCC DROPCLEANBUFFERS")
    resumo("python-tds", True, msgs, xml, True, "(numero da mensagem disponivel: 3612/3613/3615)")


def pymssql_():
    import pymssql
    c = pymssql.connect(H, "sa", PW, DB, port=str(cfg.port), autocommit=True)
    got = []
    c._conn.set_msghandler(lambda *a: got.append(a[-1].decode()))  # API de baixo nivel, sem numero de mensagem
    cur = c.cursor()
    cur.execute(SETS + Q); cur.fetchall()
    while cur.nextset():
        pass
    cur.execute("SET STATISTICS XML ON; " + Q)
    xml = False
    r = [cur.fetchall()]
    while cur.nextset():
        r.append(cur.fetchall())
    xml = any("<ShowPlanXML" in str(x) for x in r)
    cur.execute("CHECKPOINT; DBCC DROPCLEANBUFFERS")
    resumo("pymssql", True, got, xml, True, "(so via _conn.set_msghandler, sem numero; cursor.messages inexistente)")


def mssql_python_():
    import mssql_python
    c = mssql_python.connect(f"SERVER={H},{cfg.port};DATABASE={DB};UID=sa;PWD={PW};Encrypt=yes;TrustServerCertificate=yes", autocommit=True)
    cur = c.cursor()
    msgs = []
    cur.execute(SETS + Q)
    while True:
        msgs += [m[0] + " " + m[1] for m in cur.messages]  # cursor.messages e limpo a cada nextset
        if cur.description:
            cur.fetchone()
        if not cur.nextset():
            break
    msgs += [m[0] + " " + m[1] for m in cur.messages]
    cur.execute("SET STATISTICS XML ON; " + Q)
    xml = False
    while True:
        if cur.description:
            try:
                xml = xml or "<ShowPlanXML" in str(cur.fetchone()[0])  # fetchall falha com o XML (SQLGetData failed)
            except Exception:
                pass
        if not cur.nextset():
            break
    cur.execute("CHECKPOINT; DBCC DROPCLEANBUFFERS")
    t = time.perf_counter()
    for _ in range(50):
        cur.execute("SELECT 1"); cur.fetchall()
    resumo("mssql-python", True, msgs, xml, True, f"(driver ODBC 18 embutido no wheel; ida e volta SELECT 1 = {(time.perf_counter()-t)/50*1000:.1f} ms)")


def pyodbc_():
    import pyodbc
    print("pyodbc        drivers ODBC:", pyodbc.drivers())


for nome, f in [("python-tds", pytds_), ("pymssql", pymssql_), ("mssql-python", mssql_python_), ("pyodbc", pyodbc_)]:
    try:
        f()
    except Exception as e:
        print(f"{nome:13} FALHOU: {type(e).__name__}: {str(e).splitlines()[0][:150]}")
