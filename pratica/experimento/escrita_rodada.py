#!/usr/bin/env python
"""Rodada dedicada de CUSTO DE ESCRITA (substitui os dados de escrita da R1 e da R2-compat160).

Motivo: na R1 o DML usava `UPDATE TOP (k)` e `DELETE TOP (k)` sem ORDER BY, cujo conjunto de linhas depende do caminho de
acesso (com o indice presente o plano pode varrer o indice nao clusterizado e tocar outras linhas). Sintomas: razoes de
log abaixo de 1 no DELETE e razao 0,00 no UPDATE de Users.Location (primeiras linhas com Location nulo). Corrigido em
tcclab/escrita.py (alvo = intervalo da chave clusterizada; UPDATE que sempre muda o valor). Esta rodada mede, num clone limpo:
  - cada indice DISTINTO da prescricao do framework (catalogo.toml: `indice`, `indice_original` e extras);
  - cada indice sugerido pelo otimizador (braco nativo) que difere do indice do framework (lido de coleta_consulta.nativo_ddl
    da rodada indicada em --de-rodada).
Uso (em pratica/nucleo): .venv/bin/python ../experimento/escrita_rodada.py --rodada escrita-corrigida --de-rodada r1-compat160
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coletar  # noqa: E402

from tcclab import catalogo, escrita, medir  # noqa: E402
from tcclab.config import Config  # noqa: E402
from tcclab.driver import conectar  # noqa: E402
from tcclab.store import Armazem  # noqa: E402


def normalizar(create: str):
    """Assinatura do indice (tabela, chave ordenada, INCLUDE como conjunto): ignora colchetes, espacos e o nome."""
    return catalogo._assinatura_indice(create)


def coletar_indices(cat, de_rodada: str | None) -> list[dict]:
    """[{origem, query_ids, create}] sem repeticao (por texto normalizado)."""
    vistos: dict[str, dict] = {}

    def add(origem: str, qid: str, create: str | None) -> None:
        if not create:
            return
        chave = normalizar(create)
        d = vistos.setdefault(chave, {"origem": origem, "query_ids": [], "create": create})
        if qid not in d["query_ids"]:
            d["query_ids"].append(qid)

    for c in cat.consultas:
        add("framework", c.id, c.indice)
        add("framework", c.id, c.indice_original)
        for e in c.extras:
            add("framework_extra", c.id, e.indice)
    if de_rodada:
        db = sqlite3.connect(f"file:{coletar.AQUI / 'resultados' / de_rodada / 'tcclab.sqlite'}?mode=ro", uri=True)
        for qid, ddl in db.execute("SELECT query_id, nativo_ddl FROM coleta_consulta WHERE nativo_ddl IS NOT NULL AND status='concluido'"):
            c = cat.por_id(qid)
            if normalizar(ddl) in {normalizar(x) for x in (c.indice, c.indice_original) if x}:
                continue  # igual ao do framework: nao e braco distinto
            add("nativo", qid, ddl)
        db.close()
    return list(vistos.values())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rodada", default="escrita-corrigida")
    p.add_argument("--de-rodada", default="r1-compat160", help="rodada de onde ler os indices nativos")
    p.add_argument("--compat", type=int, default=160)
    p.add_argument("--maxdop", type=int, default=1)
    p.add_argument("--k", type=int, default=1000)
    p.add_argument("--repeticoes", type=int, default=10)
    p.add_argument("--apenas-framework", action="store_true")
    a = p.parse_args()
    cfg = Config.carregar()
    cat = catalogo.carregar(coletar.AQUI / "catalogo.toml")
    indices = coletar_indices(cat, None if a.apenas_framework else a.de_rodada)
    pasta = coletar.AQUI / "resultados" / a.rodada
    pasta.mkdir(parents=True, exist_ok=True)
    log = coletar.Log(pasta / "coleta.log")
    arm = Armazem(pasta / "tcclab.sqlite")
    arm.db.executescript(coletar.DDL_AUX)
    arm.db.executescript("CREATE TABLE IF NOT EXISTS escrita_origem (rodada TEXT, indice TEXT, origem TEXT, query_ids TEXT, create_ddl TEXT);")
    arm.db.execute("INSERT OR IGNORE INTO rodada VALUES (?,?,?,?,?)",
                   (a.rodada, coletar.agora(), a.compat, a.maxdop, json.dumps(vars(a), ensure_ascii=False)))
    arm.db.commit()
    log(f"{len(indices)} indices distintos a medir (k={a.k}, repeticoes={a.repeticoes})")
    coletar.preparar_clone(cfg, a.compat, a.maxdop)
    con = conectar(cfg, coletar.BANCO)
    try:
        medir.preparar_sessao(con)
        for d in indices:
            t0 = time.perf_counter()
            create = d["create"]
            tabela = catalogo._RE_CREATE.search(create).group(2)
            nome = catalogo._RE_CREATE.search(create).group(1)
            try:
                r = escrita.medir_custo_escrita(con, cfg, coletar.BANCO, tabela, create, k=a.k, repeticoes=a.repeticoes)
            except BaseException as e:  # noqa: BLE001
                if isinstance(e, KeyboardInterrupt):
                    raise
                log(f"[{nome}] FALHOU: {e!r}")
                continue
            rotulo = nome if d["origem"] != "nativo" else f"{nome} ({'+'.join(d['query_ids'])})"
            qid = "+".join(d["query_ids"])
            arm.db.execute("INSERT INTO escrita_origem VALUES (?,?,?,?,?)", (a.rodada, rotulo, d["origem"], qid, create))
            arm.db.execute("INSERT INTO escrita_indice VALUES (?,?,?,?,?,?,?,?)",
                           (a.rodada, qid, tabela, rotulo, create, r["k"], r["criacao_ms"], r["paginas"]))
            arm.db.executemany("INSERT INTO escrita_medicao VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               [(a.rodada, qid, m.tabela, rotulo if m.indice != "sem indice" else "sem indice", m.operacao, m.k, m.rep,
                                 m.logical_reads, m.log_bytes, m.cpu_ms, m.elapsed_ms) for m in r["medicoes"]])
            arm.db.commit()
            log(f"[{rotulo}] {d['origem']}: {r['paginas']} paginas, criacao {r['criacao_ms']:.0f} ms, {time.perf_counter() - t0:.0f}s")
    finally:
        con.fechar()
    arm.fechar()
    log("rodada de escrita concluida")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
