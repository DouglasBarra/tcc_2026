#!/usr/bin/env python
"""Coleta do experimento principal (marco M4): catalogo -> clone limpo -> medicao -> SQLite.

Por consulta do catalogo:
  1. restaura o clone so_exp do .bak (estado inicial identico) e fixa compat. e MAXDOP;
  2. confere equivalencia de resultado entre original e reescrita (hash de multiconjunto);
  3. obtem o indice sugerido pelo otimizador (plano estimado, MissingIndexes) = braco "nativo";
  4. executa o experimento (ABA; regimes quente e frio; UPDATE STATISTICS FULLSCAN antes de cada bloco);
  5. se o original medido no inicio (A1) e no fim (A2) divergir alem do limite, repete o experimento uma vez;
  6. mede o custo de escrita do indice da prescricao (INSERT/UPDATE/DELETE em transacao revertida).
O banco base StackOverflow2010 nunca e usado. Resultados so no SQLite (nunca no SQL Server medido).

Uso (na pasta pratica/nucleo): .venv/bin/python ../experimento/coletar.py --rodada piloto --consultas F2,V3 --n-quente 10
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from tcclab import canais, catalogo, escrita, exportar, medir
from tcclab.config import Config
from tcclab.driver import conectar
from tcclab.runner import PlanoExperimento, executar_experimento
from tcclab.store import Armazem

BANCO = "so_exp"
BAK = "/var/opt/mssql/backups/StackOverflow2010_original.bak"
AQUI = Path(__file__).resolve().parent

DDL_AUX = """
CREATE TABLE IF NOT EXISTS rodada (rodada TEXT PRIMARY KEY, iniciada_utc TEXT, compat INTEGER, maxdop INTEGER, parametros_json TEXT);
CREATE TABLE IF NOT EXISTS coleta_consulta (
  rodada TEXT, query_id TEXT, experiment_id INTEGER, tentativa INTEGER, status TEXT, equivalencia_json TEXT,
  nativo_ddl TEXT, notas_json TEXT, deriva_a1_a2 REAL, descartado_por_deriva INTEGER, duracao_s REAL, erro TEXT);
CREATE TABLE IF NOT EXISTS escrita_indice (rodada TEXT, query_id TEXT, tabela TEXT, indice TEXT, create_ddl TEXT, k INTEGER, criacao_ms REAL, paginas INTEGER);
CREATE TABLE IF NOT EXISTS escrita_medicao (rodada TEXT, query_id TEXT, tabela TEXT, indice TEXT, operacao TEXT, k INTEGER, rep INTEGER,
  logical_reads INTEGER, log_bytes INTEGER, cpu_ms INTEGER, elapsed_ms INTEGER);
"""


def agora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Log:
    def __init__(self, caminho: Path):
        self.f = caminho.open("a", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        linha = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        print(linha, file=sys.stderr, flush=True)
        self.f.write(linha + "\n")
        self.f.flush()


def preparar_clone(cfg: Config, compat: int, maxdop: int) -> float:
    """Recria so_exp a partir do .bak e fixa as opcoes. Devolve a duracao em segundos."""
    cfg.exigir_banco_permitido(BANCO)
    t0 = time.perf_counter()
    con = conectar(cfg, "master")
    try:
        con.executar_lote(
            f"IF DB_ID('{BANCO}') IS NOT NULL BEGIN ALTER DATABASE {BANCO} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE {BANCO}; END"
        )
        con.executar_lote(
            f"RESTORE DATABASE {BANCO} FROM DISK = '{BAK}' WITH MOVE 'StackOverflow2010' TO '/var/opt/mssql/data/{BANCO}.mdf', "
            f"MOVE 'StackOverflow2010_log' TO '/var/opt/mssql/data/{BANCO}_log.ldf', CHECKSUM"
        )
        con.executar_lote(f"ALTER DATABASE {BANCO} SET QUERY_STORE = ON")
        con.executar_lote(
            f"ALTER DATABASE {BANCO} SET QUERY_STORE (OPERATION_MODE = READ_WRITE, QUERY_CAPTURE_MODE = ALL, INTERVAL_LENGTH_MINUTES = 1, "
            "DATA_FLUSH_INTERVAL_SECONDS = 60, MAX_STORAGE_SIZE_MB = 512, SIZE_BASED_CLEANUP_MODE = AUTO, "
            "CLEANUP_POLICY = (STALE_QUERY_THRESHOLD_DAYS = 30), WAIT_STATS_CAPTURE_MODE = ON)"
        )
        con.executar_lote(f"ALTER DATABASE {BANCO} SET COMPATIBILITY_LEVEL = {compat}")
    finally:
        con.fechar()
    con = conectar(cfg, BANCO)
    try:
        con.executar_lote(f"ALTER DATABASE SCOPED CONFIGURATION SET MAXDOP = {maxdop}")
    finally:
        con.fechar()
    return time.perf_counter() - t0


def contar_indices_nao_clusterizados(cfg: Config) -> int:
    con = conectar(cfg, BANCO)
    try:
        r = con.executar_lote(
            "SELECT COUNT(*) FROM sys.indexes i JOIN sys.objects o ON o.object_id = i.object_id "
            "WHERE o.is_ms_shipped = 0 AND i.type_desc = 'NONCLUSTERED'", guardar_linhas=1)
        return int(r.conjuntos[0].linhas[0][0])
    finally:
        con.fechar()


def linhas_por_tabela(con, tabelas: list[str]) -> dict[str, int]:
    saida = {}
    for t in tabelas:
        r = con.executar_lote(
            f"SELECT SUM(row_count) FROM sys.dm_db_partition_stats WHERE object_id = OBJECT_ID('{t}') AND index_id IN (0,1)",
            guardar_linhas=1)
        saida[t] = int(r.conjuntos[0].linhas[0][0])
    return saida


def deriva_a1_a2(arm: Armazem, exp_id: int) -> float | None:
    """|mediana(A2) - mediana(A1)| / mediana(A1) da CPU (us), regime quente, bloco do original."""
    def med(bloco: str) -> float | None:
        linhas = arm.consultar(
            "SELECT COALESCE(m.cpu_us, m.cpu_ms * 1000.0) AS cpu FROM measurement m JOIN run r ON r.run_id = m.run_id "
            "WHERE r.experiment_id = ? AND r.regime = 'quente' AND r.block = ? AND m.phase = 'measured'", (exp_id, bloco))
        vals = [float(x["cpu"]) for x in linhas if x["cpu"] is not None]
        return statistics.median(vals) if vals else None
    a1, a2 = med("A1"), med("A2")
    if not a1 or a2 is None:
        return None
    return abs(a2 - a1) / a1


def executar_consulta(cfg, arm, cat, c, args, log, rodada) -> None:
    inicio = time.perf_counter()
    dur_clone = preparar_clone(cfg, args.compat, args.maxdop)
    log(f"[{c.id}] clone restaurado em {dur_clone:.1f}s (compat {args.compat}, MAXDOP {args.maxdop})")
    con = conectar(cfg, BANCO)
    try:
        medir.preparar_sessao(con)
        equiv = catalogo.verificar_equivalencia(con, c)
        log(f"[{c.id}] equivalencia: {equiv.get('ok')} ({equiv.get('linhas')} linhas)" if equiv["aplicavel"] else f"[{c.id}] sem reescrita")
        linhas_antes = linhas_por_tabela(con, c.tabelas)
        nativo = None
        if c.indice:  # so ha braco nativo quando ha indice a prescrever
            _, pa = canais.capturar_plano_estimado(con, c.sql_original)
            ins = pa.principal()
            nativo = catalogo.indice_nativo(ins.indices_ausentes) if ins else None
            log(f"[{c.id}] indice sugerido pelo otimizador: {nativo or 'nenhum'}")
    finally:
        con.fechar()
    if not equiv["ok"]:
        arm.db.execute("INSERT INTO coleta_consulta(rodada,query_id,tentativa,status,equivalencia_json) VALUES (?,?,0,'invalida_equivalencia',?)",
                       (rodada, c.id, json.dumps(equiv, default=str)))
        arm.db.commit()
        log(f"[{c.id}] RESULTADO DIVERGENTE: consulta invalidada, nao medida")
        return
    variantes, notas = catalogo.variantes_de(c, nativo)
    for tentativa in (1, 2):
        plano = PlanoExperimento(
            nome=f"{rodada}:{c.id}" + ("" if tentativa == 1 else f":t{tentativa}"), database=BANCO, variantes=variantes,
            regimes=tuple(args.regimes), aquecimentos=args.aquecimentos,
            repeticoes={"quente": args.n_quente, "frio": args.n_frio}, ordem="ABA", semente=args.semente + tentativa - 1,
            cache_planos="escopo", estatisticas_tabelas=tuple(c.tabelas), consulta_id=c.id, titulo=c.titulo,
        )
        t0 = time.perf_counter()
        try:
            res = executar_experimento(cfg, arm, plano, progresso=log)
        except BaseException as e:  # noqa: BLE001
            log(f"[{c.id}] ERRO no experimento: {e!r}")
            arm.db.execute("INSERT INTO coleta_consulta(rodada,query_id,tentativa,status,erro,nativo_ddl,notas_json) VALUES (?,?,?,'erro',?,?,?)",
                           (rodada, c.id, tentativa, traceback.format_exc()[-3000:], nativo, json.dumps(notas)))
            arm.db.commit()
            raise
        exp_id = res["experiment_id"]
        d = deriva_a1_a2(arm, exp_id) if "quente" in args.regimes else None
        descarta = int(d is not None and d > args.limite_deriva and tentativa == 1 and args.repetir_por_deriva)
        arm.db.execute(
            "INSERT INTO coleta_consulta VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (rodada, c.id, exp_id, tentativa, "concluido", json.dumps(equiv, default=str), nativo, json.dumps(notas, ensure_ascii=False),
             d, descarta, time.perf_counter() - t0, None))
        arm.db.commit()
        arm.db.execute("UPDATE catalog_query SET antipattern_id = ?, selectivity_class = ?, notes = ? WHERE query_id = ?",
                       (c.antipadrao, c.seletividade, c.papel, c.id))
        arm.db.commit()
        log(f"[{c.id}] experimento {exp_id} concluido em {time.perf_counter() - t0:.0f}s; deriva A1->A2 = " + (f"{d:.1%}" if d is not None else "n/d"))
        if not descarta:
            break
        log(f"[{c.id}] deriva acima de {args.limite_deriva:.0%}: repetindo o experimento (a tentativa 1 fica marcada como descartada)")
    if args.escrita and c.indice:
        con = conectar(cfg, BANCO)
        try:
            medir.preparar_sessao(con)
            tabela = c.tabelas[-1] if len(c.tabelas) == 1 else _tabela_do_indice(c.indice)
            r = escrita.medir_custo_escrita(con, cfg, BANCO, tabela, c.indice, k=args.k_escrita, repeticoes=args.n_escrita)
            arm.db.execute("INSERT INTO escrita_indice VALUES (?,?,?,?,?,?,?,?)",
                           (rodada, c.id, tabela, r["indice"], c.indice, r["k"], r["criacao_ms"], r["paginas"]))
            arm.db.executemany("INSERT INTO escrita_medicao VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               [(rodada, c.id, m.tabela, m.indice, m.operacao, m.k, m.rep, m.logical_reads, m.log_bytes, m.cpu_ms, m.elapsed_ms)
                                for m in r["medicoes"]])
            arm.db.commit()
            depois = linhas_por_tabela(con, c.tabelas)
            if depois != linhas_antes:
                log(f"[{c.id}] ATENCAO: contagem de linhas mudou apos a microcarga: {linhas_antes} -> {depois}")
            log(f"[{c.id}] custo de escrita medido ({tabela}; indice {r['paginas']} paginas; criacao {r['criacao_ms']:.0f} ms)")
        finally:
            con.fechar()
    n_nc = contar_indices_nao_clusterizados(cfg)
    if n_nc:
        log(f"[{c.id}] ATENCAO: {n_nc} indice(s) nao clusterizado(s) restante(s) no clone")
    log(f"[{c.id}] total {time.perf_counter() - inicio:.0f}s")


def _tabela_do_indice(create: str) -> str:
    return catalogo._RE_CREATE.search(create).group(2)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rodada", required=True, help="nome da rodada (pasta em resultados/)")
    p.add_argument("--catalogo", default=str(AQUI / "catalogo.toml"))
    p.add_argument("--consultas", help="ids separados por virgula (padrao: todas)")
    p.add_argument("--compat", type=int, default=160)
    p.add_argument("--maxdop", type=int, default=1)
    p.add_argument("--regimes", default="quente,frio")
    p.add_argument("--n-quente", type=int, default=20)
    p.add_argument("--n-frio", type=int, default=8)
    p.add_argument("--aquecimentos", type=int, default=2)
    p.add_argument("--semente", type=int, default=20261025)
    p.add_argument("--limite-deriva", type=float, default=0.25)
    p.add_argument("--sem-repetir-por-deriva", dest="repetir_por_deriva", action="store_false")
    p.add_argument("--sem-escrita", dest="escrita", action="store_false")
    p.add_argument("--k-escrita", type=int, default=1000)
    p.add_argument("--n-escrita", type=int, default=10)
    p.add_argument("--saida", default=str(AQUI / "resultados"))
    args = p.parse_args()
    args.regimes = [r.strip() for r in args.regimes.split(",") if r.strip()]

    cfg = Config.carregar()
    cat = catalogo.carregar(args.catalogo)
    alvo = [cat.por_id(i.strip()) for i in args.consultas.split(",")] if args.consultas else cat.consultas
    pasta = Path(args.saida) / args.rodada
    pasta.mkdir(parents=True, exist_ok=True)
    log = Log(pasta / "coleta.log")
    arm = Armazem(pasta / "tcclab.sqlite")
    arm.db.executescript(DDL_AUX)
    arm.db.execute("INSERT OR IGNORE INTO rodada VALUES (?,?,?,?,?)",
                   (args.rodada, agora(), args.compat, args.maxdop, json.dumps(vars(args), default=str, ensure_ascii=False)))
    arm.db.commit()
    log(f"rodada {args.rodada}: {len(alvo)} consulta(s), regimes {args.regimes}, N quente={args.n_quente}, N frio={args.n_frio}, compat={args.compat}, MAXDOP={args.maxdop}")
    falhas = []
    t0 = time.perf_counter()
    for c in alvo:
        try:
            executar_consulta(cfg, arm, cat, c, args, log, args.rodada)
        except BaseException as e:  # noqa: BLE001
            if isinstance(e, KeyboardInterrupt):
                raise
            falhas.append(c.id)
            log(f"[{c.id}] FALHOU: {e!r}")
    for r in arm.consultar("SELECT experiment_id FROM coleta_consulta WHERE rodada = ? AND status = 'concluido' AND descartado_por_deriva = 0", (args.rodada,)):
        exportar.exportar(arm, r["experiment_id"], pasta / "export" / f"exp{r['experiment_id']}", "csv")
    log(f"rodada concluida em {(time.perf_counter() - t0) / 60:.1f} min; falhas: {falhas or 'nenhuma'}")
    arm.fechar()
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
