"""Canais de medicao B (plano real em XML) e C/D (sys.dm_exec_query_stats e Query Store).

Canal A (STATISTICS IO/TIME) esta em mensagens.py + medir.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from .driver import Conexao
from .plano import PlanoAnalisado, analisar_plano


# ---------------------------------------------------------------- plano real (canal B)
def capturar_plano_real(con: Conexao, sql: str) -> tuple[str, PlanoAnalisado]:
    """Executa `sql` UMA vez com STATISTICS XML e devolve (xml, plano analisado).
    Fora do conjunto medido: coletar o plano altera o tempo."""
    con.executar_lote("SET STATISTICS XML ON")
    try:
        res = con.executar_lote(sql)
    finally:
        con.executar_lote("SET STATISTICS XML OFF")
    planos = res.planos_xml()
    if not planos:
        raise RuntimeError("O servidor nao devolveu plano XML (permissao SHOWPLAN?)")
    return planos[0], analisar_plano(planos[0])


def capturar_plano_estimado(con: Conexao, sql: str) -> tuple[str, PlanoAnalisado]:
    """Plano estimado (nao executa). SHOWPLAN_XML exige lote proprio."""
    con.executar_lote("SET SHOWPLAN_XML ON")
    try:
        res = con.executar_lote(sql)
    finally:
        con.executar_lote("SET SHOWPLAN_XML OFF")
    planos = res.planos_xml()
    if not planos:
        raise RuntimeError("O servidor nao devolveu plano estimado")
    return planos[0], analisar_plano(planos[0])


# ---------------------------------------------------------------- sys.dm_exec_query_stats
@dataclass
class AmostraDMV:
    query_plan_hash: str
    execution_count: int
    total_logical_reads: int
    total_physical_reads: int
    total_worker_time_us: int
    total_elapsed_time_us: int
    min_worker_time_us: int
    max_worker_time_us: int
    min_elapsed_time_us: int
    max_elapsed_time_us: int
    last_worker_time_us: int
    last_elapsed_time_us: int

    def menos(self, antes: "AmostraDMV | None") -> "AmostraDMV":
        """Delta (execucoes feitas entre dois instantaneos). min/max/last ficam os do instante final."""
        if antes is None:
            return self
        return AmostraDMV(
            self.query_plan_hash,
            self.execution_count - antes.execution_count,
            self.total_logical_reads - antes.total_logical_reads,
            self.total_physical_reads - antes.total_physical_reads,
            self.total_worker_time_us - antes.total_worker_time_us,
            self.total_elapsed_time_us - antes.total_elapsed_time_us,
            self.min_worker_time_us, self.max_worker_time_us,
            self.min_elapsed_time_us, self.max_elapsed_time_us,
            self.last_worker_time_us, self.last_elapsed_time_us,
        )

    @property
    def media_leituras_logicas(self) -> float | None:
        return self.total_logical_reads / self.execution_count if self.execution_count > 0 else None

    @property
    def media_cpu_us(self) -> float | None:
        return self.total_worker_time_us / self.execution_count if self.execution_count > 0 else None

    @property
    def media_elapsed_us(self) -> float | None:
        return self.total_elapsed_time_us / self.execution_count if self.execution_count > 0 else None


def _hash_literal(h: str) -> str:
    if not (h.startswith("0x") and all(c in "0123456789abcdefABCDEF" for c in h[2:])):
        raise ValueError(f"hash invalido: {h!r}")
    return h


def instantaneo_dmv(con: Conexao, query_hash: str) -> dict[str, AmostraDMV]:
    """Soma, por query_plan_hash, as linhas de dm_exec_query_stats com o query_hash dado."""
    qh = _hash_literal(query_hash)
    res = con.executar_lote(
        f"""
        SELECT CONVERT(varchar(18), query_plan_hash, 1),
               SUM(CONVERT(bigint, execution_count)), SUM(total_logical_reads), SUM(total_physical_reads),
               SUM(total_worker_time), SUM(total_elapsed_time),
               MIN(min_worker_time), MAX(max_worker_time), MIN(min_elapsed_time), MAX(max_elapsed_time),
               MAX(last_worker_time), MAX(last_elapsed_time)
        FROM sys.dm_exec_query_stats
        WHERE query_hash = CONVERT(binary(8), '{qh}', 1)
        GROUP BY query_plan_hash""",
        guardar_linhas=50,
    )
    saida: dict[str, AmostraDMV] = {}
    for r in res.conjuntos[0].linhas if res.conjuntos else []:
        saida[r[0]] = AmostraDMV(r[0], *[int(x) for x in r[1:]])
    return saida


def ultima_execucao_dmv(con: Conexao, query_hash: str, query_plan_hash: str | None = None) -> dict | None:
    """Metricas em MICROSSEGUNDOS da ultima execucao (last_worker_time, last_elapsed_time, last_logical_reads).
    So e correta se a execucao imediatamente anterior, nesta instancia, foi da consulta de `query_hash`
    (o runner chama logo apos cada repeticao; nao ha concorrencia no ambiente de teste)."""
    qh = _hash_literal(query_hash)
    filtro_plano = f" AND query_plan_hash = CONVERT(binary(8), '{_hash_literal(query_plan_hash)}', 1)" if query_plan_hash else ""
    res = con.executar_lote(
        f"""SELECT TOP 1 last_worker_time, last_elapsed_time, last_logical_reads, last_physical_reads, execution_count
            FROM sys.dm_exec_query_stats WHERE query_hash = CONVERT(binary(8), '{qh}', 1){filtro_plano}
            ORDER BY last_execution_time DESC""",
        guardar_linhas=1,
    )
    if not res.conjuntos or not res.conjuntos[0].linhas:
        return None
    r = res.conjuntos[0].linhas[0]
    return {"cpu_us": int(r[0]), "elapsed_us": int(r[1]), "logical_reads": int(r[2]),
            "physical_reads": int(r[3]), "execution_count": int(r[4])}


def delta_dmv(antes: dict[str, AmostraDMV], depois: dict[str, AmostraDMV]) -> dict[str, AmostraDMV]:
    out = {}
    for h, d in depois.items():
        dd = d.menos(antes.get(h))
        if dd.execution_count > 0:
            out[h] = dd
    return out


# ---------------------------------------------------------------- Query Store
@dataclass
class TotaisQS:
    """Totais acumulados (soma de avg*count) de um plano no Query Store, em todos os intervalos."""
    plan_id: int
    query_id: int
    query_plan_hash: str
    count: int
    soma_duration_us: float
    soma_cpu_us: float
    soma_logical: float
    soma_physical: float
    intervalos: int


@dataclass
class AmostraQS:
    plan_id: int
    query_id: int
    query_plan_hash: str
    count_executions: int
    avg_duration_us: float
    avg_cpu_us: float
    avg_logical_io_reads: float
    avg_physical_io_reads: float
    intervalos: int


def flush_query_store(con: Conexao) -> None:
    con.executar_lote("EXEC sys.sp_query_store_flush_db")


def instantaneo_qs(con: Conexao, query_hash: str) -> dict[int, TotaisQS]:
    """Totais por plano (todos os intervalos) para o query_hash. Faz flush antes de ler.

    O Query Store agrega por intervalo de tempo (1 min em tcc_lab) e por plano; execucoes de blocos
    diferentes caem no mesmo intervalo. Por isso o experimento usa a DIFERENCA entre dois instantaneos
    (antes e depois do bloco), nao a leitura direta de uma janela de tempo.
    """
    qh = _hash_literal(query_hash)
    flush_query_store(con)
    res = con.executar_lote(
        f"""
        SELECT p.plan_id, q.query_id, CONVERT(varchar(18), p.query_plan_hash, 1),
               SUM(rs.count_executions),
               SUM(rs.avg_duration * rs.count_executions),
               SUM(rs.avg_cpu_time * rs.count_executions),
               SUM(rs.avg_logical_io_reads * rs.count_executions),
               SUM(rs.avg_physical_io_reads * rs.count_executions),
               COUNT(DISTINCT rs.runtime_stats_interval_id)
        FROM sys.query_store_runtime_stats rs
        JOIN sys.query_store_plan p ON p.plan_id = rs.plan_id
        JOIN sys.query_store_query q ON q.query_id = p.query_id
        WHERE q.query_hash = CONVERT(binary(8), '{qh}', 1)
        GROUP BY p.plan_id, q.query_id, p.query_plan_hash""",
        guardar_linhas=100,
    )
    out: dict[int, TotaisQS] = {}
    for r in res.conjuntos[0].linhas if res.conjuntos else []:
        out[int(r[0])] = TotaisQS(int(r[0]), int(r[1]), r[2], int(r[3]), float(r[4]), float(r[5]),
                                  float(r[6]), float(r[7]), int(r[8]))
    return out


def delta_qs(antes: dict[int, TotaisQS], depois: dict[int, TotaisQS]) -> list[AmostraQS]:
    out = []
    for pid, d in depois.items():
        a = antes.get(pid)
        n = d.count - (a.count if a else 0)
        if n <= 0:
            continue
        f = lambda campo: getattr(d, campo) - (getattr(a, campo) if a else 0.0)  # noqa: E731
        out.append(AmostraQS(pid, d.query_id, d.query_plan_hash, n, f("soma_duration_us") / n,
                             f("soma_cpu_us") / n, f("soma_logical") / n, f("soma_physical") / n, d.intervalos))
    return out
