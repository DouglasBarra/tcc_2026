"""Instantaneo do ambiente (tabela `environment`) e deteccao de atividade que invalida medicoes de tempo."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import platform
import sys
from pathlib import Path

from . import __version__
from .driver import Conexao


def _um(con: Conexao, sql: str) -> tuple:
    res = con.executar_lote(sql, guardar_linhas=1)
    return res.conjuntos[0].linhas[0] if res.conjuntos and res.conjuntos[0].linhas else ()


def atividade_em_segundo_plano(con: Conexao) -> list[str]:
    """Lista motivos pelos quais medicoes de TEMPO nao sao confiaveis agora:
    restauracao/backup em andamento, banco fora de ONLINE, outras requisicoes de usuario ativas."""
    avisos: list[str] = []
    res = con.executar_lote(
        """SELECT r.session_id, r.command, DB_NAME(r.database_id), r.percent_complete
           FROM sys.dm_exec_requests r
           WHERE r.session_id <> @@SPID AND r.session_id > 50
             AND (r.command LIKE 'RESTORE%' OR r.command LIKE 'BACKUP%' OR r.command LIKE 'DBCC%'
                  OR r.command LIKE 'CREATE%' OR r.command LIKE 'ALTER%' OR r.command LIKE 'BULK%'
                  OR r.command LIKE 'INSERT%' OR r.command LIKE 'SELECT%' OR r.command LIKE 'UPDATE%')""",
        guardar_linhas=50,
    )
    for r in res.conjuntos[0].linhas if res.conjuntos else []:
        avisos.append(f"sessao {r[0]} executando {r[1]} em {r[2]} ({r[3]:.0f}% concluido)")
    res = con.executar_lote(
        "SELECT name, state_desc FROM sys.databases WHERE state_desc <> 'ONLINE'", guardar_linhas=50
    )
    for r in res.conjuntos[0].linhas if res.conjuntos else []:
        avisos.append(f"banco {r[0]} em estado {r[1]} (restauracao/recuperacao em andamento?)")
    return avisos


def capturar_ambiente(con: Conexao, database: str, driver: str = "python-tds") -> dict:
    versao = _um(con, "SELECT @@VERSION")[0]
    edicao, produto = _um(con, "SELECT CONVERT(varchar(100),SERVERPROPERTY('Edition')), CONVERT(varchar(50),SERVERPROPERTY('ProductVersion'))")
    compat = _um(con, f"SELECT compatibility_level FROM sys.databases WHERE name = N'{database.replace(chr(39), '')}'")
    cpu, mem_mb = _um(con, "SELECT cpu_count, physical_memory_kb/1024 FROM sys.dm_os_sys_info")
    cfgs = dict(
        con.executar_lote(
            "SELECT name, CONVERT(int, value_in_use) FROM sys.configurations "
            "WHERE name IN ('max server memory (MB)','max degree of parallelism','cost threshold for parallelism')",
            guardar_linhas=10,
        ).conjuntos[0].linhas
    )
    esc = dict(
        con.executar_lote(
            "SELECT name, CONVERT(varchar(60), value) FROM sys.database_scoped_configurations "
            "WHERE name IN ('MAXDOP','LEGACY_CARDINALITY_ESTIMATION','PARAMETER_SNIFFING','QUERY_OPTIMIZER_HOTFIXES')",
            guardar_linhas=10,
        ).conjuntos[0].linhas
    )
    maxdop_escopo = int(esc["MAXDOP"]) if esc.get("MAXDOP", "").isdigit() else None
    plataforma = _um(con, "SELECT host_platform, host_distribution FROM sys.dm_os_host_info")
    qs = con.executar_lote(
        "SELECT actual_state_desc, query_capture_mode_desc, interval_length_minutes, "
        "flush_interval_seconds, max_storage_size_mb FROM sys.database_query_store_options",
        guardar_linhas=1,
    ).conjuntos[0].linhas
    lock = Path(__file__).resolve().parents[2] / "requirements.lock"
    return {
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "sql_version": versao.split("\n")[0].strip(),
        "edition": edicao,
        "product_version": produto,
        "compat_level": compat[0] if compat else None,
        "ce_version": compat[0] if compat and compat[0] >= 120 else (70 if compat else None),  # CE segue o nivel de compatibilidade (doc); conferir em CardinalityEstimationModelVersion do plano
        "cpu_count": cpu,
        "physical_memory_mb": mem_mb,
        "max_server_memory_mb": cfgs.get("max server memory (MB)"),
        "maxdop": cfgs.get("max degree of parallelism"),
        "cost_threshold": cfgs.get("cost threshold for parallelism"),
        "config_escopo_banco": esc,  # MAXDOP do escopo do banco (0 = herda o do servidor) e opcoes do otimizador
        "maxdop_efetivo": maxdop_escopo if maxdop_escopo else cfgs.get("max degree of parallelism"),
        "container_platform": f"{plataforma[0]} / {plataforma[1]}" if plataforma else None,
        "emulacao_amd64_em_arm64": platform.machine() == "arm64",  # o host e arm64; o container e amd64 (compose)
        "query_store_options": list(qs[0]) if qs else None,
        "tool_version": __version__,
        "tool_commit": None,  # a pasta nao e repositorio git
        "python_version": sys.version.split()[0],
        "python_lock_hash": hashlib.sha256(lock.read_bytes()).hexdigest()[:16] if lock.is_file() else None,
        "driver": driver,
        "database": database,
        "avisos": atividade_em_segundo_plano(con),
    }


def para_json(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, default=str)
