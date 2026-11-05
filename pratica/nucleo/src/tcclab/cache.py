"""Controle de cache para os regimes frio e quente.

Frio  = CHECKPOINT + DBCC DROPCLEANBUFFERS (frio so para o buffer pool do SQL Server; o cache de
        arquivos do Linux/VM do Docker e do macOS NAO e limpo, ver relatorio 19, secao 5.3).
Quente = aquecimento (execucoes descartadas) ate o plano compilar e as paginas entrarem no buffer pool.
Plano = limpeza do cache de planos so em ambiente de teste (banco na lista de permissao).

DBCC DROPCLEANBUFFERS e DBCC FREEPROCCACHE sao de INSTANCIA: afetam todos os bancos (inclusive
outro dataset carregado na mesma instancia). Por isso exigem banco permitido e sao usados com cuidado.
"""
from __future__ import annotations

from .config import Config
from .driver import Conexao


def preparar_frio(con: Conexao, cfg: Config, database: str) -> None:
    cfg.exigir_banco_permitido(database)
    # CHECKPOINT grava paginas sujas do banco corrente; sem isso o DROPCLEANBUFFERS nao as remove.
    con.executar_lote("CHECKPOINT")
    con.executar_lote("DBCC DROPCLEANBUFFERS")


def limpar_cache_planos(con: Conexao, cfg: Config, database: str, modo: str = "escopo") -> None:
    """modo='escopo': ALTER DATABASE SCOPED CONFIGURATION CLEAR PROCEDURE_CACHE (so o banco corrente);
    modo='global': DBCC FREEPROCCACHE (instancia inteira)."""
    cfg.exigir_banco_permitido(database)
    if modo == "escopo":
        con.executar_lote("ALTER DATABASE SCOPED CONFIGURATION CLEAR PROCEDURE_CACHE")
    elif modo == "global":
        con.executar_lote("DBCC FREEPROCCACHE")
    else:
        raise ValueError(f"modo de limpeza invalido: {modo}")


def aquecer(con: Conexao, sql: str, vezes: int) -> None:
    for _ in range(vezes):
        con.executar_lote(sql)
