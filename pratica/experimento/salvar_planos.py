#!/usr/bin/env python
"""Salva o plano estimado e o plano real (1 execucao) de cada consulta do catalogo, na linha de base (sem indices).

Saida: pratica/experimento/planos/<id>/{original,reescrita}.{estimado,real}.sqlplan e indice.json com os
atributos extraidos. Serve de fixture para o Diagnostico (sem precisar do banco) e de evidencia no apendice.
Uso (em pratica/nucleo): .venv/bin/python ../experimento/salvar_planos.py [--compat 160] [--consultas F1,S1]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coletar  # noqa: E402

from tcclab import canais, catalogo, medir  # noqa: E402
from tcclab.config import Config  # noqa: E402
from tcclab.driver import conectar  # noqa: E402

SAIDA = Path(__file__).resolve().parent / "planos"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--compat", type=int, default=160)
    p.add_argument("--consultas")
    p.add_argument("--catalogo", default=str(coletar.AQUI / "catalogo.toml"))
    p.add_argument("--saida", default=str(SAIDA), help="pasta de saida (padrao: experimento/planos)")
    p.add_argument("--sem-real", action="store_true", help="so planos estimados")
    a = p.parse_args()
    cfg = Config.carregar()
    cat = catalogo.carregar(a.catalogo)
    alvo = [cat.por_id(i) for i in a.consultas.split(",")] if a.consultas else cat.consultas
    saida = Path(a.saida)
    coletar.preparar_clone(cfg, a.compat, 1)
    con = conectar(cfg, coletar.BANCO)
    resumo = {}
    try:
        medir.preparar_sessao(con)
        for c in alvo:
            pasta = saida / c.id
            pasta.mkdir(parents=True, exist_ok=True)
            resumo[c.id] = {"antipadrao": c.antipadrao, "titulo": c.titulo, "sql_original": c.sql_original, "sql_reescrita": c.sql_reescrita, "planos": {}}
            for rotulo, sql in (("original", c.sql_original), ("reescrita", c.sql_reescrita)):
                if not sql:
                    continue
                modos = [("estimado", canais.capturar_plano_estimado)]
                if not a.sem_real:
                    modos.append(("real", canais.capturar_plano_real))
                for modo, fn in modos:
                    xml, pa = fn(con, sql)
                    (pasta / f"{rotulo}.{modo}.sqlplan").write_text(xml, encoding="utf-8")
                    ins = pa.principal()
                    resumo[c.id]["planos"][f"{rotulo}.{modo}"] = {
                        "query_plan_hash": ins.query_plan_hash if ins else None,
                        "custo": ins.custo_subarvore if ins else None,
                        "leituras_plano": ins.leituras_logicas_plano if ins and ins.eh_plano_real else None,
                        "indices_ausentes": ins.indices_ausentes if ins else [],
                        "avisos": ins.avisos if ins else [],
                        "operadores": [f"{o.physical_op}:{o.objeto or ''}" for o in (ins.operadores if ins else [])],
                    }
            print(f"[{c.id}] planos salvos", file=sys.stderr, flush=True)
    finally:
        con.fechar()
    (saida / "indice.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"{len(resumo)} consultas em {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
