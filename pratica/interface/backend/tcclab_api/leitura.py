"""Leitura SOMENTE LEITURA dos SQLite de resultados (esquema de tcclab.store + tabelas auxiliares de coletar.py).

Nunca abre o SQL Server. Toda conexao SQLite e aberta com `mode=ro`; nenhuma funcao daqui escreve.
Tabelas auxiliares (rodada, coleta_consulta, escrita_indice, escrita_medicao) podem nao existir
(por exemplo, resultados do M0 ou de uma validacao ao vivo): cada funcao trata a ausencia.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from tcclab.store import VERSAO_ESQUEMA

from .config import Configuracao

NOME_RODADA = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,80}$")   # "_abortada-..." (coleta interrompida e renomeada) tambem aparece
PREFIXO_AO_VIVO = "ao-vivo-"
METRICAS_RUN = ("logical_reads", "cpu_ms", "elapsed_ms")


class RodadaInexistente(LookupError):
    pass


class RodadaIncompativel(RuntimeError):
    pass


@dataclass(frozen=True)
class RodadaRef:
    nome: str
    pasta: Path
    origem: str   # "coleta" | "ao_vivo"

    @property
    def sqlite(self) -> Path:
        return self.pasta / "tcclab.sqlite"

    @property
    def tipo(self) -> str:
        if self.origem == "ao_vivo":
            return "ao_vivo"
        if self.nome.startswith("_abortada"):
            return "abortada"
        return "piloto" if self.nome.startswith("piloto") else "coleta"


def descobrir_rodadas(cfg: Configuracao) -> list[RodadaRef]:
    achadas: list[RodadaRef] = []
    for raiz, origem, prefixo in ((cfg.resultados, "coleta", ""), (cfg.ao_vivo_dir, "ao_vivo", PREFIXO_AO_VIVO)):
        if not raiz.is_dir():
            continue
        for d in sorted(raiz.iterdir()):
            if d.is_dir() and (d / "tcclab.sqlite").is_file():
                nome = prefixo + d.name if origem == "ao_vivo" and not d.name.startswith(prefixo) else d.name
                if NOME_RODADA.match(nome):
                    achadas.append(RodadaRef(nome, d, origem))
    return achadas


def achar_rodada(cfg: Configuracao, nome: str) -> RodadaRef:
    if not NOME_RODADA.match(nome):
        raise RodadaInexistente(nome)
    for r in descobrir_rodadas(cfg):
        if r.nome == nome:
            return r
    raise RodadaInexistente(nome)


@contextmanager
def abrir(ref: RodadaRef) -> Iterator[sqlite3.Connection]:
    """Abre o SQLite em modo somente leitura e confere a versao do esquema."""
    uri = ref.sqlite.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=2.0)
    con.row_factory = sqlite3.Row
    try:
        v = con.execute("PRAGMA user_version").fetchone()[0]
        if v != VERSAO_ESQUEMA:
            raise RodadaIncompativel(f"{ref.nome}: esquema v{v}; a interface espera v{VERSAO_ESQUEMA}")
        yield con
    finally:
        con.close()


def tem_tabela(con: sqlite3.Connection, nome: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nome,)).fetchone() is not None


def colunas(con: sqlite3.Connection, tabela: str) -> set[str]:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({tabela})")}


def _json(txt: str | None, padrao=None):
    if txt in (None, ""):
        return padrao
    try:
        return json.loads(txt)
    except (TypeError, ValueError):
        return padrao


# ---------------------------------------------------------------------------------------- rodadas
def idade_s(ref: RodadaRef) -> float:
    """Segundos desde a ultima modificacao do SQLite ou do log da coleta (o que for mais recente)."""
    candidatos = [ref.sqlite, ref.pasta / "coleta.log"]
    mt = max((p.stat().st_mtime for p in candidatos if p.exists()), default=0.0)
    return max(0.0, time.time() - mt)


def resumo_rodada(cfg: Configuracao, ref: RodadaRef) -> dict:
    base = {"rodada": ref.nome, "tipo": ref.tipo, "origem": ref.origem,
            "modificada_ha_s": round(idade_s(ref), 1), "compativel": True, "motivo_incompativel": None}
    try:
        with abrir(ref) as con:
            exps = [dict(r) for r in con.execute("SELECT experiment_id, name, status, started_at, finished_at FROM experiment ORDER BY experiment_id")]
            n_runs = con.execute("SELECT COUNT(*) FROM run").fetchone()[0]
            n_med = con.execute("SELECT COUNT(*) FROM measurement WHERE phase='measured'").fetchone()[0]
            consultas = [r[0] for r in con.execute("SELECT DISTINCT query_id FROM variant ORDER BY query_id")]
            params = None
            if tem_tabela(con, "rodada"):
                r = con.execute("SELECT * FROM rodada LIMIT 1").fetchone()
                if r:
                    params = {"compat": r["compat"], "maxdop": r["maxdop"], "iniciada_utc": r["iniciada_utc"],
                              "parametros": _json(r["parametros_json"], {})}
            descartes = 0
            if tem_tabela(con, "coleta_consulta"):
                descartes = con.execute("SELECT COUNT(*) FROM coleta_consulta WHERE descartado_por_deriva = 1").fetchone()[0]
    except RodadaIncompativel as e:
        return base | {"compativel": False, "motivo_incompativel": str(e)}
    except sqlite3.DatabaseError as e:   # inclui OperationalError (ocupado) e arquivo corrompido/journal pendente
        return base | {"compativel": False, "motivo_incompativel": f"banco ocupado ou ilegível: {e}"}
    executando = [e for e in exps if e["status"] == "executando"]
    em_andamento = bool(executando) and base["modificada_ha_s"] < cfg.janela_coleta_ativa_s
    if em_andamento:
        estado = "em_andamento"
    elif executando:
        estado = "interrompida"   # status 'executando' sem atividade recente: a coleta parou antes de fechar o experimento
    elif any(e["status"] == "erro" for e in exps):
        estado = "com_erro"
    elif exps:
        estado = "concluida"
    else:
        estado = "vazia"
    return base | {
        "estado": estado, "n_experimentos": len(exps), "n_runs": n_runs, "n_medicoes": n_med,
        "consultas": consultas, "experimentos": exps, "parametros_rodada": params,
        "n_descartados_por_deriva": descartes,
    }


def coletas_em_andamento(cfg: Configuracao) -> list[dict]:
    """Rodadas de COLETA com experimento 'executando' e atividade recente (outro processo esta medindo)."""
    saida = []
    for ref in descobrir_rodadas(cfg):
        if ref.origem != "coleta" or ref.tipo == "abortada":
            continue
        r = resumo_rodada(cfg, ref)
        if r.get("estado") == "em_andamento":
            saida.append({"rodada": ref.nome, "modificada_ha_s": r["modificada_ha_s"]})
    return saida


# ---------------------------------------------------------------------------------------- experimentos / consultas
def experimentos_da_consulta(con: sqlite3.Connection, query_id: str) -> list[dict]:
    """Experimentos que tem runs de variantes da consulta, com tentativa/descarte de coleta_consulta quando existir."""
    rows = con.execute(
        """SELECT DISTINCT e.experiment_id, e.name, e.status, e.started_at, e.finished_at, e.env_id, e.seed
           FROM experiment e JOIN run r ON r.experiment_id = e.experiment_id
           JOIN variant v ON v.variant_id = r.variant_id WHERE v.query_id = ? ORDER BY e.experiment_id""",
        (query_id,),
    )
    saida = [dict(r) for r in rows]
    extra: dict[int, dict] = {}
    if tem_tabela(con, "coleta_consulta"):
        for r in con.execute("SELECT * FROM coleta_consulta WHERE query_id = ? AND experiment_id IS NOT NULL", (query_id,)):
            extra[r["experiment_id"]] = dict(r)
    for e in saida:
        c = extra.get(e["experiment_id"])
        e["tentativa"] = c["tentativa"] if c else None
        e["descartado_por_deriva"] = bool(c["descartado_por_deriva"]) if c else False
        e["deriva_a1_a2"] = c["deriva_a1_a2"] if c else None
    return saida


def experimento_padrao(exps: list[dict]) -> dict | None:
    validos = [e for e in exps if not e["descartado_por_deriva"]] or exps
    return max(validos, key=lambda e: e["experiment_id"]) if validos else None


def consultas_medidas(con: sqlite3.Connection) -> dict[str, list[dict]]:
    """query_id -> experimentos (id, status) que tem runs. Uma consulta so, para o catalogo marcar 'medida'."""
    saida: dict[str, list[dict]] = {}
    for r in con.execute(
        """SELECT DISTINCT v.query_id, e.experiment_id, e.status FROM variant v
           JOIN run r ON r.variant_id = v.variant_id JOIN experiment e ON e.experiment_id = r.experiment_id
           ORDER BY v.query_id, e.experiment_id"""
    ):
        saida.setdefault(r["query_id"], []).append({"experiment_id": r["experiment_id"], "status": r["status"]})
    return saida


def variantes_do_experimento(con: sqlite3.Connection, experiment_id: int) -> list[dict]:
    rows = con.execute(
        """SELECT DISTINCT v.variant_id, v.query_id, v.kind, v.nome, v.sql_text, v.ddl_apply, v.ddl_revert
           FROM variant v JOIN run r ON r.variant_id = v.variant_id WHERE r.experiment_id = ? ORDER BY v.variant_id""",
        (experiment_id,),
    )
    return [dict(r) for r in rows]


def runs_do_experimento(con: sqlite3.Connection, experiment_id: int) -> list[dict]:
    rows = con.execute(
        """SELECT r.run_id, r.experiment_id, r.seq, r.regime, r.block, r.status, r.started_at, r.finished_at,
                  r.query_hash, r.query_plan_hash, r.plan_id, r.avisos_json,
                  v.variant_id, v.nome AS variante, v.kind, v.query_id
           FROM run r JOIN variant v ON v.variant_id = r.variant_id WHERE r.experiment_id = ? ORDER BY r.seq""",
        (experiment_id,),
    )
    saida = []
    for r in rows:
        d = dict(r)
        d["avisos"] = _json(d.pop("avisos_json"), [])
        saida.append(d)
    return saida


def valores_medidos(con: sqlite3.Connection, run_ids: list[int], metricas: tuple[str, ...]) -> dict[int, dict[str, list[float]]]:
    """run_id -> metrica -> valores (fase 'measured', na ordem das repeticoes, sem nulos)."""
    if not run_ids:
        return {}
    ph = ",".join("?" for _ in run_ids)
    cols = ", ".join(metricas)
    saida: dict[int, dict[str, list[float]]] = {rid: {m: [] for m in metricas} for rid in run_ids}
    for r in con.execute(f"SELECT run_id, rep, {cols} FROM measurement WHERE phase='measured' AND run_id IN ({ph}) ORDER BY run_id, rep", run_ids):
        for m in metricas:
            if r[m] is not None:
                saida[r["run_id"]][m].append(float(r[m]))
    return saida


def repeticoes(con: sqlite3.Connection, experiment_id: int, metrica: str) -> list[dict]:
    """Todas as repeticoes medidas de uma metrica, para os graficos de pontos. `metrica` e validada pelo chamador."""
    rows = con.execute(
        f"""SELECT r.run_id, r.regime, r.block, v.variant_id, v.nome AS variante, v.kind, m.rep, m.{metrica} AS valor
            FROM measurement m JOIN run r ON r.run_id = m.run_id JOIN variant v ON v.variant_id = r.variant_id
            WHERE r.experiment_id = ? AND m.phase = 'measured' AND m.{metrica} IS NOT NULL ORDER BY r.seq, m.rep""",
        (experiment_id,),
    )
    return [dict(r) for r in rows]


def metricas_validas(con: sqlite3.Connection) -> set[str]:
    cols = colunas(con, "measurement")
    return {c for c in cols if c in ("logical_reads", "physical_reads", "read_ahead_reads", "lob_logical_reads",
                                     "scan_count", "cpu_ms", "elapsed_ms", "cpu_us", "elapsed_us", "client_ms",
                                     "compile_cpu_ms", "compile_elapsed_ms", "rows")}


def linha_coleta(con: sqlite3.Connection, experiment_id: int) -> dict | None:
    if not tem_tabela(con, "coleta_consulta"):
        return None
    r = con.execute("SELECT * FROM coleta_consulta WHERE experiment_id = ?", (experiment_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["equivalencia"] = _json(d.pop("equivalencia_json"), None)
    d["notas"] = _json(d.pop("notas_json"), [])
    return d


def registro_coleta(con: sqlite3.Connection, query_id: str) -> list[dict]:
    """Todas as linhas de coleta_consulta da consulta (tentativas, descartes, invalidacoes, erros), na ordem de gravacao."""
    if not tem_tabela(con, "coleta_consulta"):
        return []
    out = []
    for r in con.execute("SELECT rowid AS linha, * FROM coleta_consulta WHERE query_id = ? ORDER BY rowid", (query_id,)):
        d = dict(r)
        d["equivalencia"] = _json(d.pop("equivalencia_json"), None)
        d["notas"] = _json(d.pop("notas_json"), [])
        d["descartado_por_deriva"] = bool(d["descartado_por_deriva"]) if d["descartado_por_deriva"] is not None else None
        out.append(d)
    return out


def coleta_sem_experimento(con: sqlite3.Connection, query_id: str) -> list[dict]:
    """Linhas de coleta_consulta sem experimento (invalidada por equivalencia, erro): tambem sao resultado."""
    if not tem_tabela(con, "coleta_consulta"):
        return []
    out = []
    for r in con.execute("SELECT * FROM coleta_consulta WHERE query_id = ? AND experiment_id IS NULL", (query_id,)):
        d = dict(r)
        d["equivalencia"] = _json(d.pop("equivalencia_json"), None)
        d["notas"] = _json(d.pop("notas_json"), [])
        out.append(d)
    return out


# ---------------------------------------------------------------------------------------- ambiente
ROTULOS_QUERY_STORE = ("estado", "modo_captura", "intervalo_min", "flush_s", "max_storage_mb")  # ordem do SELECT em tcclab.ambiente


def ambientes(con: sqlite3.Connection) -> list[dict]:
    saida = []
    uso: dict[int, list[int]] = {}
    for r in con.execute("SELECT experiment_id, env_id FROM experiment ORDER BY experiment_id"):
        uso.setdefault(r["env_id"], []).append(r["experiment_id"])
    for r in con.execute("SELECT * FROM environment ORDER BY env_id"):
        d = dict(r)
        bruto = _json(d.pop("bruto_json", None), {}) or {}
        # campos gravados so no JSON bruto (coletas recentes de tcclab.ambiente); None = instantaneo anterior a eles
        d["config_escopo_banco"] = bruto.get("config_escopo_banco")
        d["maxdop_efetivo"] = bruto.get("maxdop_efetivo")
        qs = _json(d.pop("query_store_options_json"), None)
        d["query_store"] = dict(zip(ROTULOS_QUERY_STORE, qs)) if isinstance(qs, list) else None
        d["avisos"] = _json(d.pop("avisos_json"), [])
        d["emulacao_amd64_em_arm64"] = None if d["emulacao_amd64_em_arm64"] is None else bool(d["emulacao_amd64_em_arm64"])
        d["experimentos"] = uso.get(d["env_id"], [])
        saida.append(d)
    return saida


# ---------------------------------------------------------------------------------------- planos
def plano_por_id(con: sqlite3.Connection, plan_id: int) -> dict | None:
    r = con.execute("SELECT * FROM plan WHERE plan_id = ?", (plan_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["atributos"] = _json(d.pop("atributos_json"), {})
    d["runs"] = [dict(x) for x in con.execute(
        """SELECT r.run_id, r.experiment_id, r.regime, r.block, v.nome AS variante, v.query_id
           FROM run r JOIN variant v ON v.variant_id = r.variant_id WHERE r.plan_id = ? ORDER BY r.run_id""", (plan_id,))]
    return d


# ---------------------------------------------------------------------------------------- escrita
def escrita_da_consulta(con: sqlite3.Connection, query_id: str) -> tuple[dict | None, list[dict]]:
    if not (tem_tabela(con, "escrita_indice") and tem_tabela(con, "escrita_medicao")):
        return None, []
    ind = con.execute("SELECT * FROM escrita_indice WHERE query_id = ? ORDER BY rowid DESC LIMIT 1", (query_id,)).fetchone()
    if not ind:
        return None, []
    med = [dict(r) for r in con.execute("SELECT rowid AS linha, * FROM escrita_medicao WHERE query_id = ? ORDER BY rowid", (query_id,))]
    return dict(ind), med
