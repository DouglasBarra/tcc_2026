"""Execucao de uma consulta com captura do canal A (STATISTICS IO/TIME)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .driver import Conexao
from .mensagens import EstatisticasLote, analisar

# Opcoes de sessao fixas (relatorio 16, secao 6.2 item 8). Registradas no experimento.
SESSAO_PADRAO = (
    "SET NOCOUNT ON",
    "SET LANGUAGE us_english",
    "SET ARITHABORT ON",
    "SET STATISTICS IO ON",
    "SET STATISTICS TIME ON",
)


def preparar_sessao(con: Conexao) -> None:
    """Cada SET vai em lote proprio: o efeito persiste na sessao, e as mensagens
    '3612 CPU time = 0 ms' dos proprios SET nao contaminam o lote medido."""
    for s in SESSAO_PADRAO:
        con.executar_lote(s)


@dataclass
class Medicao:
    logical_reads: int
    physical_reads: int
    read_ahead_reads: int
    lob_logical_reads: int
    scan_count: int
    cpu_ms: int
    elapsed_ms: int
    compile_cpu_ms: int
    compile_elapsed_ms: int
    client_ms: float
    rows: int
    result_hash: str | None
    io_raw: str
    time_raw: str
    ts_utc: str
    io_por_tabela: list[dict] = field(default_factory=list)
    cpu_us: int | None = None        # canal D por execucao (dm_exec_query_stats.last_worker_time), preenchido pelo runner
    elapsed_us: int | None = None
    dmv_logical_reads: int | None = None


def agora_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def medir_uma(con: Conexao, sql: str, *, hash_linhas: bool = True) -> Medicao:
    """Executa `sql` (um lote) com STATISTICS IO/TIME ligados na sessao e devolve as metricas do canal A.
    As linhas sao lidas e descartadas (so contagem e hash); o `client_ms` inclui o envio das linhas."""
    ts = agora_utc()
    res = con.executar_lote(sql, hash_linhas=hash_linhas)
    est: EstatisticasLote = analisar(res.mensagens)
    if not est.io and not est.execucao:
        raise RuntimeError(
            "Nenhuma mensagem de STATISTICS IO/TIME recebida: a sessao foi preparada com preparar_sessao()?"
        )
    dados = [c for c in res.conjuntos if not c.eh_plano_xml]
    return Medicao(
        logical_reads=est.soma("logical_reads"),
        physical_reads=est.soma("physical_reads"),
        read_ahead_reads=est.soma("read_ahead_reads"),
        lob_logical_reads=est.soma("lob_logical_reads"),
        scan_count=est.soma("scan_count"),
        cpu_ms=est.cpu_ms,
        elapsed_ms=est.elapsed_ms,
        compile_cpu_ms=est.compile_cpu_ms,
        compile_elapsed_ms=est.compile_elapsed_ms,
        client_ms=res.cliente_ms,
        rows=sum(c.total_linhas for c in dados),
        result_hash=dados[0].hash_linhas if dados else None,
        io_raw=est.io_bruto,
        time_raw=est.tempo_bruto,
        ts_utc=ts,
        io_por_tabela=[vars(t).copy() for t in est.io],
    )
