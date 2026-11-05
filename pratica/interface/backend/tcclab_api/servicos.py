"""Montagem das respostas que cruzam tabelas: Validacao (antes x depois), Historico de runs e custo de escrita.

Toda agregacao vem de `tcclab.stats.resumir` (descritiva) ou de `tcclab.analise` (razao, IC, testes) quando o
modulo existe. Cada numero devolvido carrega `run_ids` (ou `linhas` de escrita_medicao) para rastreio.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from . import leitura as L
from .adaptadores import GerenteAnalise, descritiva

METRICAS_RESUMO = ("logical_reads", "physical_reads", "read_ahead_reads", "cpu_ms", "elapsed_ms", "cpu_us", "elapsed_us")
METRICAS_TELA = ("logical_reads", "cpu_ms", "elapsed_ms")   # as tres metricas do projeto
ROTULO_SEM_INDICE = "sem indice"                            # tcclab.escrita.medir_custo_escrita
OPERACOES = ("insert", "update", "delete")


def _ids(txt: str | None) -> list[int]:
    return [int(x) for x in (txt or "").split(";") if x.strip()]


def _normalizar_linha(d: dict) -> dict:
    """Converte campos 'run_ids*' de texto 'a;b;c' (formato do tcclab.analise) para listas."""
    saida = dict(d)
    for k in ("run_ids", "run_ids_antes", "run_ids_depois"):
        if k in saida and isinstance(saida[k], str):
            saida[k] = _ids(saida[k])
    return saida


def _grupos_descritivos(runs: list[dict], valores: dict[int, dict[str, list[float]]]) -> list[dict]:
    """Fallback sem tcclab.analise: descritiva por variante x regime x metrica (todos os blocos juntos)."""
    por: dict[tuple, dict[str, Any]] = {}
    for r in runs:
        if r["status"] != "concluido":
            continue
        g = por.setdefault((r["variant_id"], r["regime"]),
                           {"variant_id": r["variant_id"], "variante": r["variante"], "kind": r["kind"],
                            "regime": r["regime"], "run_ids": [], "valores": {m: [] for m in METRICAS_RESUMO}})
        g["run_ids"].append(r["run_id"])
        for m in METRICAS_RESUMO:
            g["valores"][m] += valores.get(r["run_id"], {}).get(m, [])
    saida = []
    for g in por.values():
        for m in METRICAS_RESUMO:
            v = g["valores"][m]
            if v:
                saida.append({"variant_id": g["variant_id"], "variante": g["variante"], "kind": g["kind"], "regime": g["regime"],
                              "metrica": m, **descritiva(v), "run_ids": g["run_ids"], "ic_lo": None, "ic_hi": None})
    return saida


def tratamento_padrao(variantes: list[dict]) -> str | None:
    """Variante comparada por padrao com a original: a prescricao do framework (kind 'prescricao'), senao 'indice'."""
    for kind in ("prescricao", "indice"):
        for v in variantes:
            if v["kind"] == kind:
                return v["nome"]
    outras = [v for v in variantes if v["kind"] != "original"]
    return outras[0]["nome"] if outras else None


def montar_validacao(cfg, ref: L.RodadaRef, query_id: str, experiment_id: int | None,
                     gerente: GerenteAnalise, antipadroes: dict[str, str], papeis: dict[str, str] | None = None) -> dict:
    with L.abrir(ref) as con:
        exps = L.experimentos_da_consulta(con, query_id)
        sem_exp = L.coleta_sem_experimento(con, query_id)
        registro = L.registro_coleta(con, query_id)
        base = {"rodada": ref.nome, "tipo_rodada": ref.tipo, "query_id": query_id, "experimentos": exps, "sem_experimento": sem_exp,
                "registro_coleta": registro}
        if not exps:
            motivo = "a consulta não tem experimento nesta rodada"
            if sem_exp:
                motivo = f"a coleta registrou '{sem_exp[-1]['status']}' para esta consulta e nenhum experimento foi gravado"
            return base | {"disponivel": False, "motivo": motivo}
        esc = next((e for e in exps if e["experiment_id"] == experiment_id), None) if experiment_id else L.experimento_padrao(exps)
        if esc is None:
            return base | {"disponivel": False, "motivo": f"experimento {experiment_id} não existe para {query_id} nesta rodada"}
        eid = esc["experiment_id"]
        variantes = L.variantes_do_experimento(con, eid)
        runs = L.runs_do_experimento(con, eid)
        valores = L.valores_medidos(con, [r["run_id"] for r in runs if r["status"] == "concluido"], L.METRICAS_RUN + ("cpu_us", "elapsed_us"))
        coleta = L.linha_coleta(con, eid)
        ambiente_id = esc["env_id"]

    runs_out = []
    for r in runs:
        v = valores.get(r["run_id"], {})
        runs_out.append({
            **r, "n": len(v.get("logical_reads", [])),
            "medianas": {m: (descritiva(v[m])["mediana"] if v.get(m) else None) for m in L.METRICAS_RUN + ("cpu_us", "elapsed_us")},
        })

    avisos: list[str] = []
    if esc["status"] != "concluido":
        avisos.append(f"experimento {eid} com status '{esc['status']}': dados possivelmente parciais")
    if esc["descartado_por_deriva"]:
        avisos.append(f"experimento {eid} foi DESCARTADO pela regra de deriva (A1→A2) e substituído por nova tentativa; segue gravado")
    ativ = [r["run_id"] for r in runs if r["avisos"]]
    if ativ:
        avisos.append(f"runs {ativ} registraram aviso (atividade em segundo plano ou mais de um plano): métricas de TEMPO não são confiáveis")

    analise, motivo = gerente.por_experimento(ref, eid, antipadroes, papeis)
    if analise:
        grupos = [_normalizar_linha(x) | {"fonte": "tcclab.analise"} for x in analise["resumos"]
                  if x["experiment_id"] == eid]
        comparacoes = [_normalizar_linha(x) for x in analise["comparacoes"] if x["experiment_id"] == eid]
        veredictos = [_normalizar_linha(x) for x in analise["veredictos"] if x["experiment_id"] == eid]
        determinismo = [_normalizar_linha(x) for x in analise["determinismo"] if x["experiment_id"] == eid]
        avisos += analise["avisos"]
        meta_analise = {"disponivel": True, "motivo": None, "criterios": analise["criterios"], "criterios_sha": analise["criterios_sha"],
                        "meta": analise["meta"]}
    else:
        grupos = [g | {"fonte": "tcclab.stats"} for g in _grupos_descritivos(runs, valores)]
        comparacoes, veredictos, determinismo = [], [], []
        meta_analise = {"disponivel": False, "motivo": motivo, "criterios": None, "criterios_sha": None, "meta": None}
        lr = [g for g in grupos if g["metrica"] == "logical_reads"]
        determinismo = [{"variante": g["variante"], "kind": g["kind"], "regime": g["regime"], "n": g["n"], "dp": g["dp"],
                         "deterministico": g["dp"] == 0, "run_ids": g["run_ids"]} for g in lr]

    return base | {
        "disponivel": True, "experimento": esc, "variantes": variantes, "runs": runs_out, "grupos": grupos,
        "comparacoes": comparacoes, "veredictos": veredictos, "determinismo": determinismo,
        "tratamento_padrao": tratamento_padrao(variantes), "analise": meta_analise,
        "coleta": coleta, "env_id": ambiente_id, "avisos": list(dict.fromkeys(avisos)),
    }


# ---------------------------------------------------------------------------------------- historico de runs
def listar_runs(ref: L.RodadaRef, query_id: str | None, variante: str | None, regime: str | None,
                experiment_id: int | None, limite: int) -> dict:
    with L.abrir(ref) as con:
        where, params = ["1=1"], []
        if query_id:
            where.append("v.query_id = ?"); params.append(query_id)
        if variante:
            where.append("v.nome = ?"); params.append(variante)
        if regime:
            where.append("r.regime = ?"); params.append(regime)
        if experiment_id is not None:
            where.append("r.experiment_id = ?"); params.append(experiment_id)
        total = con.execute(
            f"SELECT COUNT(*) FROM run r JOIN variant v ON v.variant_id = r.variant_id WHERE {' AND '.join(where)}", params).fetchone()[0]
        rows = [dict(r) for r in con.execute(
            f"""SELECT r.run_id, r.experiment_id, r.seq, r.regime, r.block AS bloco, r.status, r.started_at, r.finished_at,
                       r.query_plan_hash, r.plan_id, r.avisos_json, v.variant_id, v.nome AS variante, v.kind, v.query_id, e.name AS experimento
                FROM run r JOIN variant v ON v.variant_id = r.variant_id JOIN experiment e ON e.experiment_id = r.experiment_id
                WHERE {' AND '.join(where)} ORDER BY r.run_id DESC LIMIT ?""", [*params, limite])]
        valores = L.valores_medidos(con, [r["run_id"] for r in rows], L.METRICAS_RUN)
        filtros = {
            "consultas": [r[0] for r in con.execute("SELECT DISTINCT query_id FROM variant ORDER BY query_id")],
            "variantes": [r[0] for r in con.execute("SELECT DISTINCT nome FROM variant ORDER BY nome")],
            "regimes": [r[0] for r in con.execute("SELECT DISTINCT regime FROM run ORDER BY regime")],
        }
    import json
    for r in rows:
        v = valores.get(r["run_id"], {})
        r["avisos"] = json.loads(r.pop("avisos_json") or "[]")
        r["n"] = len(v.get("logical_reads", []))
        r["medianas"] = {m: (descritiva(v[m])["mediana"] if v.get(m) else None) for m in L.METRICAS_RUN}
    return {"rodada": ref.nome, "total": total, "retornados": len(rows), "runs": rows, "filtros": filtros}


# ---------------------------------------------------------------------------------------- custo de escrita
def montar_escrita(ref: L.RodadaRef, query_id: str, gerente: GerenteAnalise) -> dict:
    with L.abrir(ref) as con:
        ind, med = L.escrita_da_consulta(con, query_id)
    if ind is None:
        return {"rodada": ref.nome, "query_id": query_id, "disponivel": False,
                "motivo": "não há medição de custo de escrita para esta consulta nesta rodada (sem índice a prescrever, ou a coleta foi feita com --sem-escrita)"}
    por: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in med:
        por[(m["operacao"], m["indice"])].append(m)
    nome_indice = ind["indice"]
    operacoes = []
    crit = gerente.mod.Criterios() if gerente.disponivel else None
    for op in OPERACOES:
        sem, com = por.get((op, ROTULO_SEM_INDICE), []), por.get((op, nome_indice), [])
        if not sem and not com:
            continue
        item: dict[str, Any] = {"operacao": op, "metricas": {}}
        for metrica in ("logical_reads", "log_bytes", "cpu_ms", "elapsed_ms"):
            a = [float(x[metrica]) for x in sem if x[metrica] is not None]
            d = [float(x[metrica]) for x in com if x[metrica] is not None]
            m: dict[str, Any] = {
                "sem_indice": descritiva(a) if a else None, "com_indice": descritiva(d) if d else None,
                "linhas_sem_indice": [x["linha"] for x in sem], "linhas_com_indice": [x["linha"] for x in com],
                "razao": None, "ic_lo": None, "ic_hi": None, "regra_zero": None,
            }
            if crit and a and d:
                import numpy as np
                r = gerente.mod.razao_medianas(m["sem_indice"]["mediana"], m["com_indice"]["mediana"], crit.piso_zero)
                lo, hi = gerente.mod.ic_razao(np.asarray(a), np.asarray(d), crit, "escrita", ref.nome, query_id, op, metrica)
                m |= {"razao": r.bruta, "razao_reg": r.reg, "regra_zero": r.regra or None, "ic_lo": lo, "ic_hi": hi}
            item["metricas"][metrica] = m
        operacoes.append(item)
    return {
        "rodada": ref.nome, "query_id": query_id, "disponivel": True,
        "indice": {"tabela": ind["tabela"], "nome": ind["indice"], "create_ddl": ind["create_ddl"], "k": ind["k"],
                   "criacao_ms": ind["criacao_ms"], "paginas": ind["paginas"], "rodada_registrada": ind["rodada"]},
        "operacoes": operacoes,
        "ratio_disponivel": crit is not None,
        "motivo_razao": None if crit else gerente.motivo,
    }
