"""Custo de escrita de um indice novo (camada Analise/Validacao do framework).

Para uma tabela e um indice, mede INSERT, UPDATE da primeira coluna-chave e DELETE de `k` linhas, sem o
indice e com ele, sempre dentro de transacao revertida (os dados ficam inalterados; so o contador de
IDENTITY avanca). Por execucao registra: leituras logicas e CPU/decorrido (STATISTICS IO/TIME) e os bytes
de log da transacao (sys.dm_tran_database_transactions), que sao deterministicos e medem o trabalho de manutencao.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .catalogo import _RE_CREATE, ddl_reverter
from .config import Config
from .driver import Conexao
from .medir import medir_uma

OPERACOES = ("insert", "update", "delete")

_SQL_LOG = (
    "SELECT database_transaction_log_bytes_used FROM sys.dm_tran_database_transactions "
    "WHERE transaction_id = (SELECT transaction_id FROM sys.dm_tran_current_transaction) AND database_id = DB_ID()"
)


@dataclass
class MedicaoEscrita:
    tabela: str
    indice: str            # "sem indice" ou o nome do indice
    operacao: str
    k: int
    rep: int
    logical_reads: int
    log_bytes: int | None
    cpu_ms: int
    elapsed_ms: int


def _colunas_insercao(con: Conexao, tabela: str) -> list[str]:
    r = con.executar_lote(
        "SELECT c.name FROM sys.columns c JOIN sys.types t ON t.user_type_id = c.user_type_id "
        f"WHERE c.object_id = OBJECT_ID('{tabela}') AND c.is_identity = 0 AND c.is_computed = 0 "
        "AND t.name NOT IN ('timestamp','rowversion') ORDER BY c.column_id",
        guardar_linhas=200,
    )
    return [f"[{linha[0]}]" for linha in r.conjuntos[0].linhas]


def _expressao_update(con: Conexao, tabela: str, coluna: str) -> str:
    """Expressao que SEMPRE muda o valor da coluna-chave (inclusive quando anulavel), para o UPDATE mexer no indice."""
    r = con.executar_lote(
        "SELECT t.name, c.max_length FROM sys.columns c JOIN sys.types t ON t.user_type_id = c.user_type_id "
        f"WHERE c.object_id = OBJECT_ID('{tabela}') AND c.name = '{coluna}'",
        guardar_linhas=1,
    )
    tipo, max_bytes = r.conjuntos[0].linhas[0][0], int(r.conjuntos[0].linhas[0][1])
    c = f"[{coluna}]"
    if tipo in ("int", "bigint", "smallint"):
        return f"ISNULL({c}, 0) + 1"
    if tipo in ("datetime", "datetime2", "smalldatetime"):
        return f"DATEADD(second, 1, ISNULL({c}, '19000101'))"
    if tipo in ("nvarchar", "varchar", "nchar", "char"):
        n = max_bytes // 2 if tipo.startswith("n") else max_bytes
        if n <= 1:
            raise ValueError(f"coluna {coluna} curta demais para o UPDATE de manutencao")
        v = f"ISNULL({c}, N'')"
        return f"CASE WHEN LEN({v}) < {n} THEN {v} + N'x' ELSE STUFF({v}, 1, 1, N'y') END"
    raise ValueError(f"tipo nao suportado para UPDATE de manutencao: {tipo}")


def _primeira_chave(create: str) -> str:
    m = _RE_CREATE.search(create)
    chave = re.search(r"\(([^)]*)\)", create[m.end():]).group(1)
    return chave.split(",")[0].strip().strip("[]").split()[0].strip("[]")


def comandos_dml(con: Conexao, tabela: str, create: str, k: int) -> dict[str, str]:
    """Os tres comandos tocam as MESMAS k linhas com e sem o indice: o alvo e um intervalo da chave clusterizada
    (Id <= k-esimo menor Id), nao `TOP (k)` sem ORDER BY, cujo conjunto de linhas depende do caminho de acesso."""
    cols = ", ".join(_colunas_insercao(con, tabela))
    col_chave = _primeira_chave(create)
    expr = _expressao_update(con, tabela, col_chave)
    r = con.executar_lote(f"SELECT MAX(Id) FROM (SELECT TOP ({k}) Id FROM {tabela} ORDER BY Id) AS x", guardar_linhas=1)
    hi = int(r.conjuntos[0].linhas[0][0])
    return {
        "insert": f"INSERT INTO {tabela} ({cols}) SELECT TOP ({k}) {cols} FROM {tabela} ORDER BY Id",
        "update": f"UPDATE {tabela} SET [{col_chave}] = {expr} WHERE Id <= {hi}",
        "delete": f"DELETE FROM {tabela} WHERE Id <= {hi}",
    }


def _uma_execucao(con: Conexao, dml: str) -> tuple[int, int | None, int, int]:
    con.executar_lote("BEGIN TRAN")
    try:
        m = medir_uma(con, dml, hash_linhas=False)
        r = con.executar_lote(_SQL_LOG, guardar_linhas=1)
        linhas = r.conjuntos[0].linhas if r.conjuntos else []
        log = int(linhas[0][0]) if linhas else None
        return m.logical_reads, log, m.cpu_ms, m.elapsed_ms
    finally:
        con.executar_lote("IF @@TRANCOUNT > 0 ROLLBACK")


def verificar_linhas_afetadas(con: Conexao, dml: str, esperado: int) -> bool:
    con.executar_lote("BEGIN TRAN")
    try:
        r = con.executar_lote(f"{dml}; SELECT @@ROWCOUNT", guardar_linhas=1)
        return int(r.conjuntos[-1].linhas[0][0]) == esperado
    finally:
        con.executar_lote("IF @@TRANCOUNT > 0 ROLLBACK")


def tamanho_indice_paginas(con: Conexao, tabela: str, nome_indice: str) -> int | None:
    r = con.executar_lote(
        "SELECT SUM(ps.used_page_count) FROM sys.dm_db_partition_stats ps JOIN sys.indexes i "
        "ON i.object_id = ps.object_id AND i.index_id = ps.index_id "
        f"WHERE ps.object_id = OBJECT_ID('{tabela}') AND i.name = '{nome_indice}'",
        guardar_linhas=1,
    )
    v = r.conjuntos[0].linhas[0][0] if r.conjuntos and r.conjuntos[0].linhas else None
    return int(v) if v is not None else None


def medir_custo_escrita(
    con: Conexao, cfg: Config, database: str, tabela: str, create: str, *, k: int = 1000, repeticoes: int = 10, aquecimentos: int = 1
) -> dict:
    """Mede as tres operacoes sem e com o indice. Cria e remove o indice (sempre removido ao final)."""
    cfg.exigir_banco_permitido(database)
    m = _RE_CREATE.search(create)
    nome_indice = m.group(1)
    dml = comandos_dml(con, tabela, create, k)
    for op, sql in dml.items():
        if not verificar_linhas_afetadas(con, sql, k):
            raise RuntimeError(f"{op} em {tabela} nao afetou {k} linhas")
    medicoes: list[MedicaoEscrita] = []
    info: dict = {"tabela": tabela, "indice": nome_indice, "create": create, "k": k}

    def rodar(rotulo: str) -> None:
        for op in OPERACOES:
            for _ in range(aquecimentos):
                _uma_execucao(con, dml[op])
            for rep in range(1, repeticoes + 1):
                lr, log, cpu, el = _uma_execucao(con, dml[op])
                medicoes.append(MedicaoEscrita(tabela, rotulo, op, k, rep, lr, log, cpu, el))

    rodar("sem indice")
    import time

    t0 = time.perf_counter()
    con.executar_lote(create)
    info["criacao_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    try:
        info["paginas"] = tamanho_indice_paginas(con, tabela, nome_indice)
        rodar(nome_indice)
    finally:
        con.executar_lote(ddl_reverter(create))
    info["medicoes"] = medicoes
    return info
