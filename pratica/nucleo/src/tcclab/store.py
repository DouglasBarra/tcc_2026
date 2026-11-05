"""Armazenamento em SQLite com o esquema da secao 5.1 do relatorio 16 (subconjunto de medicao do M0).

Tabelas: environment, catalog_query, variant, experiment, run, measurement, measurement_io,
qs_sample, dmv_sample, plan. (analysis, finding, column_stat, prescription, write_benchmark,
stats_summary e comparison ficam para M1 a M4.) Resultados NUNCA vao para o SQL Server medido.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

VERSAO_ESQUEMA = 2  # v2: cpu_us/elapsed_us/dmv_logical_reads em measurement; qs_sample sem min/max

DDL = """
CREATE TABLE IF NOT EXISTS environment (
  env_id INTEGER PRIMARY KEY AUTOINCREMENT,
  captured_at_utc TEXT, sql_version TEXT, edition TEXT, product_version TEXT, compat_level INTEGER,
  ce_version INTEGER, cpu_count INTEGER, physical_memory_mb INTEGER, max_server_memory_mb INTEGER,
  maxdop INTEGER, cost_threshold INTEGER, container_platform TEXT, emulacao_amd64_em_arm64 INTEGER,
  query_store_options_json TEXT, tool_version TEXT, tool_commit TEXT, python_version TEXT,
  python_lock_hash TEXT, driver TEXT, database TEXT, dataset_id TEXT, dataset_checksum TEXT,
  avisos_json TEXT, bruto_json TEXT);
CREATE TABLE IF NOT EXISTS catalog_query (
  query_id TEXT PRIMARY KEY, antipattern_id TEXT, title TEXT, sql_original TEXT, params_json TEXT,
  selectivity_class TEXT, expected_rule_ids TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS variant (
  variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
  query_id TEXT REFERENCES catalog_query(query_id),
  kind TEXT, nome TEXT, sql_text TEXT, ddl_apply TEXT, ddl_revert TEXT, schema_hash TEXT);
CREATE TABLE IF NOT EXISTS experiment (
  experiment_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT, manifest_json TEXT, env_id INTEGER REFERENCES environment(env_id),
  seed INTEGER, ordem_executada_json TEXT, started_at TEXT, finished_at TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS run (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id INTEGER REFERENCES experiment(experiment_id),
  variant_id INTEGER REFERENCES variant(variant_id),
  seq INTEGER, regime TEXT, block TEXT, status TEXT, started_at TEXT, finished_at TEXT,
  query_hash TEXT, query_plan_hash TEXT, plan_id INTEGER, avisos_json TEXT);
CREATE TABLE IF NOT EXISTS measurement (
  measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER REFERENCES run(run_id), rep INTEGER, phase TEXT,
  logical_reads INTEGER, physical_reads INTEGER, read_ahead_reads INTEGER, lob_logical_reads INTEGER,
  scan_count INTEGER, cpu_ms INTEGER, elapsed_ms INTEGER, compile_cpu_ms INTEGER, compile_elapsed_ms INTEGER,
  client_ms REAL, rows INTEGER, result_hash TEXT, plan_hash TEXT, io_raw TEXT, time_raw TEXT, ts_utc TEXT,
  cpu_us INTEGER, elapsed_us INTEGER, dmv_logical_reads INTEGER);
CREATE TABLE IF NOT EXISTS measurement_io (
  run_id INTEGER REFERENCES run(run_id), rep INTEGER, tabela TEXT, scan_count INTEGER,
  logical_reads INTEGER, physical_reads INTEGER, read_ahead_reads INTEGER, lob_logical_reads INTEGER);
CREATE TABLE IF NOT EXISTS qs_sample (
  run_id INTEGER REFERENCES run(run_id), plan_id INTEGER, query_id INTEGER, query_plan_hash TEXT,
  count_executions INTEGER, execucoes_esperadas INTEGER, avg_duration_us REAL, avg_cpu_us REAL,
  avg_logical_io_reads REAL, avg_physical_io_reads REAL, intervalos INTEGER);
CREATE TABLE IF NOT EXISTS dmv_sample (
  run_id INTEGER REFERENCES run(run_id), query_plan_hash TEXT, execution_count INTEGER,
  avg_logical_reads REAL, avg_cpu_us REAL, avg_elapsed_us REAL,
  min_cpu_us INTEGER, max_cpu_us INTEGER, min_elapsed_us INTEGER, max_elapsed_us INTEGER,
  last_cpu_us INTEGER, last_elapsed_us INTEGER);
CREATE TABLE IF NOT EXISTS plan (
  plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
  xml_sha TEXT, kind TEXT, path TEXT, query_hash TEXT, query_plan_hash TEXT, hash_formato TEXT,
  statement_cost REAL, dop INTEGER, plan_logical_reads INTEGER, atributos_json TEXT);
CREATE INDEX IF NOT EXISTS ix_measurement_run ON measurement(run_id, phase);
"""


class Armazem:
    def __init__(self, caminho: str | Path):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.caminho)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        existente = self.db.execute("PRAGMA user_version").fetchone()[0]
        if existente not in (0, VERSAO_ESQUEMA):
            raise RuntimeError(f"{self.caminho}: esquema v{existente}, o codigo espera v{VERSAO_ESQUEMA}; use outro arquivo")
        self.db.executescript(DDL)
        self.db.execute(f"PRAGMA user_version = {VERSAO_ESQUEMA}")
        self.db.commit()
        self.pasta_planos = self.caminho.parent / (self.caminho.stem + "_planos")

    def fechar(self) -> None:
        self.db.close()

    def _ins(self, tabela: str, dados: dict) -> int:
        cols = ", ".join(dados)
        marc = ", ".join("?" for _ in dados)
        cur = self.db.execute(f"INSERT INTO {tabela} ({cols}) VALUES ({marc})", list(dados.values()))
        return int(cur.lastrowid)

    # ------------------------------------------------------------- ambiente / experimento
    def novo_ambiente(self, amb: dict) -> int:
        qs = amb.get("query_store_options")
        eid = self._ins(
            "environment",
            {
                "captured_at_utc": amb["captured_at_utc"], "sql_version": amb["sql_version"], "edition": amb["edition"],
                "product_version": amb["product_version"], "compat_level": amb["compat_level"],
                "ce_version": amb["ce_version"], "cpu_count": amb["cpu_count"],
                "physical_memory_mb": amb["physical_memory_mb"], "max_server_memory_mb": amb["max_server_memory_mb"],
                "maxdop": amb["maxdop"], "cost_threshold": amb["cost_threshold"],
                "container_platform": amb["container_platform"],
                "emulacao_amd64_em_arm64": int(bool(amb["emulacao_amd64_em_arm64"])),
                "query_store_options_json": json.dumps(qs) if qs is not None else None,
                "tool_version": amb["tool_version"], "tool_commit": amb["tool_commit"],
                "python_version": amb["python_version"], "python_lock_hash": amb["python_lock_hash"],
                "driver": amb["driver"], "database": amb["database"],
                "avisos_json": json.dumps(amb["avisos"], ensure_ascii=False),
                "bruto_json": json.dumps(amb, ensure_ascii=False, default=str),
            },
        )
        self.db.commit()
        return eid

    def novo_experimento(self, nome: str, manifesto: dict, env_id: int, semente: int, inicio: str) -> int:
        eid = self._ins(
            "experiment",
            {"name": nome, "manifest_json": json.dumps(manifesto, ensure_ascii=False), "env_id": env_id,
             "seed": semente, "started_at": inicio, "status": "executando"},
        )
        self.db.commit()
        return eid

    def fechar_experimento(self, experiment_id: int, status: str, fim: str, ordem: list) -> None:
        self.db.execute(
            "UPDATE experiment SET status=?, finished_at=?, ordem_executada_json=? WHERE experiment_id=?",
            (status, fim, json.dumps(ordem, ensure_ascii=False), experiment_id),
        )
        self.db.commit()

    def garantir_consulta(self, query_id: str, titulo: str, sql: str, antipadrao: str | None = None) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO catalog_query(query_id, antipattern_id, title, sql_original) VALUES (?,?,?,?)",
            (query_id, antipadrao, titulo, sql),
        )
        self.db.commit()

    def nova_variante(self, query_id: str, nome: str, kind: str, sql: str, ddl_apply: str | None, ddl_revert: str | None) -> int:
        vid = self._ins(
            "variant",
            {"query_id": query_id, "kind": kind, "nome": nome, "sql_text": sql, "ddl_apply": ddl_apply,
             "ddl_revert": ddl_revert, "schema_hash": None},
        )
        self.db.commit()
        return vid

    # ------------------------------------------------------------- runs e medicoes
    def novo_run(self, experiment_id: int, variant_id: int, seq: int, regime: str, bloco: str, inicio: str) -> int:
        rid = self._ins(
            "run",
            {"experiment_id": experiment_id, "variant_id": variant_id, "seq": seq, "regime": regime,
             "block": bloco, "status": "executando", "started_at": inicio},
        )
        self.db.commit()
        return rid

    def fechar_run(self, run_id: int, status: str, fim: str, avisos: list[str]) -> None:
        self.db.execute(
            "UPDATE run SET status=?, finished_at=?, avisos_json=? WHERE run_id=?",
            (status, fim, json.dumps(avisos, ensure_ascii=False), run_id),
        )
        self.db.commit()

    def gravar_medicao(self, run_id: int, rep: int, fase: str, m) -> None:
        self._ins(
            "measurement",
            {"run_id": run_id, "rep": rep, "phase": fase, "logical_reads": m.logical_reads,
             "physical_reads": m.physical_reads, "read_ahead_reads": m.read_ahead_reads,
             "lob_logical_reads": m.lob_logical_reads, "scan_count": m.scan_count, "cpu_ms": m.cpu_ms,
             "elapsed_ms": m.elapsed_ms, "compile_cpu_ms": m.compile_cpu_ms,
             "compile_elapsed_ms": m.compile_elapsed_ms, "client_ms": m.client_ms, "rows": m.rows,
             "result_hash": m.result_hash, "io_raw": m.io_raw, "time_raw": m.time_raw, "ts_utc": m.ts_utc,
             "cpu_us": m.cpu_us, "elapsed_us": m.elapsed_us, "dmv_logical_reads": m.dmv_logical_reads},
        )
        for t in m.io_por_tabela:
            self._ins(
                "measurement_io",
                {"run_id": run_id, "rep": rep, "tabela": t["tabela"], "scan_count": t["scan_count"],
                 "logical_reads": t["logical_reads"], "physical_reads": t["physical_reads"],
                 "read_ahead_reads": t["read_ahead_reads"], "lob_logical_reads": t["lob_logical_reads"]},
            )

    def gravar_plano(self, xml: str, kind: str, ins) -> int:
        sha = hashlib.sha256(xml.encode("utf-8")).hexdigest()[:16]
        self.pasta_planos.mkdir(parents=True, exist_ok=True)
        caminho = self.pasta_planos / f"{sha}.sqlplan"
        if not caminho.exists():
            caminho.write_text(xml, encoding="utf-8")
        pid = self._ins(
            "plan",
            {"xml_sha": sha, "kind": kind, "path": str(caminho), "query_hash": ins.query_hash,
             "query_plan_hash": ins.query_plan_hash, "hash_formato": ins.hash_formato,
             "statement_cost": ins.custo_subarvore, "dop": ins.grau_paralelismo,
             "plan_logical_reads": ins.leituras_logicas_plano if ins.eh_plano_real else None,
             "atributos_json": json.dumps(
                 {"nivel_otimizacao": ins.nivel_otimizacao, "ce": ins.versao_ce, "cpu_ms": ins.cpu_ms,
                  "elapsed_ms": ins.elapsed_ms, "compile_time_ms": ins.compile_time_ms,
                  "operadores": [o.physical_op for o in ins.operadores], "avisos": ins.avisos},
                 ensure_ascii=False)},
        )
        self.db.commit()
        return pid

    def marcar_plano_no_run(self, run_id: int, plan_id: int, query_hash: str | None, query_plan_hash: str | None) -> None:
        self.db.execute(
            "UPDATE run SET plan_id=?, query_hash=?, query_plan_hash=? WHERE run_id=?",
            (plan_id, query_hash, query_plan_hash, run_id),
        )
        self.db.execute("UPDATE measurement SET plan_hash=? WHERE run_id=?", (query_plan_hash, run_id))
        self.db.commit()

    def gravar_qs(self, run_id: int, a, esperadas: int) -> None:
        self._ins(
            "qs_sample",
            {"run_id": run_id, "plan_id": a.plan_id, "query_id": a.query_id, "query_plan_hash": a.query_plan_hash,
             "count_executions": a.count_executions, "execucoes_esperadas": esperadas,
             "avg_duration_us": a.avg_duration_us, "avg_cpu_us": a.avg_cpu_us,
             "avg_logical_io_reads": a.avg_logical_io_reads, "avg_physical_io_reads": a.avg_physical_io_reads,
             "intervalos": a.intervalos},
        )
        self.db.commit()

    def gravar_dmv(self, run_id: int, a) -> None:
        self._ins(
            "dmv_sample",
            {"run_id": run_id, "query_plan_hash": a.query_plan_hash, "execution_count": a.execution_count,
             "avg_logical_reads": a.media_leituras_logicas, "avg_cpu_us": a.media_cpu_us,
             "avg_elapsed_us": a.media_elapsed_us, "min_cpu_us": a.min_worker_time_us,
             "max_cpu_us": a.max_worker_time_us, "min_elapsed_us": a.min_elapsed_time_us,
             "max_elapsed_us": a.max_elapsed_time_us, "last_cpu_us": a.last_worker_time_us,
             "last_elapsed_us": a.last_elapsed_time_us},
        )
        self.db.commit()

    # ------------------------------------------------------------- leitura
    def consultar(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return list(self.db.execute(sql, params))
