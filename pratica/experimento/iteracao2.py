#!/usr/bin/env python
"""Iteracao 2 do framework (POS-HOC, fora do pre-registro) para as consultas que nao atingiram o criterio na R1.

Regra do framework: sem melhoria significativa, o processo volta ao Diagnostico. Na R1, S2 (6,4%) nao atingiu 20% e S3
(28,9%) passou por pouco; o indice nativo mostrou que o custo dominante era a varredura de Posts pelo filtro externo
(OwnerUserId), nao a tabela interna da juncao. Esta rodada mede, para S2 e S3:
  original | prescricao da iteracao 1 (reescrita + indice interno) | so o indice do filtro externo (consulta original) |
  prescricao da iteracao 2 (reescrita + indice interno + indice do filtro externo) | original.
Rotulo obrigatorio no artigo: analise pos-hoc, nao pre-registrada, exploratoria.
Uso (em pratica/nucleo): .venv/bin/python ../experimento/iteracao2.py --rodada r1-iteracao2 [--consultas S2,S3]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coletar  # noqa: E402

from tcclab import catalogo  # noqa: E402
from tcclab.config import Config  # noqa: E402
from tcclab.runner import PlanoExperimento, Variante, executar_experimento  # noqa: E402
from tcclab.store import Armazem  # noqa: E402

EXTERNO = "CREATE INDEX ix_exp_posts_owner ON dbo.Posts (OwnerUserId)"
DROP_EXTERNO = "DROP INDEX ix_exp_posts_owner ON dbo.Posts"


def variantes(c: catalogo.Consulta) -> list[Variante]:
    interno = c.indice
    drop_interno = catalogo.ddl_reverter(interno)
    return [
        Variante("original", c.sql_original, kind="original"),
        Variante("iteracao 1: reescrita + indice interno", c.sql_reescrita, interno, drop_interno, kind="prescricao"),
        Variante("so o indice do filtro externo (consulta original)", c.sql_original, EXTERNO, DROP_EXTERNO, kind="indice_externo"),
        Variante("iteracao 2: reescrita + indice interno + indice externo", c.sql_reescrita,
                 f"{interno}; {EXTERNO}", f"{drop_interno}; {DROP_EXTERNO}", kind="prescricao_iteracao2"),
    ]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rodada", default="r1-iteracao2")
    p.add_argument("--consultas", default="S2,S3")
    p.add_argument("--compat", type=int, default=160)
    p.add_argument("--maxdop", type=int, default=1)
    p.add_argument("--n-quente", type=int, default=20)
    p.add_argument("--n-frio", type=int, default=8)
    p.add_argument("--aquecimentos", type=int, default=2)
    p.add_argument("--semente", type=int, default=20261026)
    a = p.parse_args()
    cfg = Config.carregar()
    cat = catalogo.carregar(coletar.AQUI / "catalogo.toml")
    pasta = Path(coletar.AQUI / "resultados" / a.rodada)
    pasta.mkdir(parents=True, exist_ok=True)
    log = coletar.Log(pasta / "coleta.log")
    arm = Armazem(pasta / "tcclab.sqlite")
    arm.db.executescript(coletar.DDL_AUX)
    arm.db.execute("INSERT OR IGNORE INTO rodada VALUES (?,?,?,?,?)", (a.rodada, coletar.agora(), a.compat, a.maxdop,
                   '{"posthoc": true, "motivo": "iteracao 2 do framework para consultas abaixo do criterio na R1"}'))
    arm.db.commit()
    for qid in [x.strip() for x in a.consultas.split(",")]:
        c = cat.por_id(qid)
        coletar.preparar_clone(cfg, a.compat, a.maxdop)
        plano = PlanoExperimento(
            nome=f"{a.rodada}:{qid}", database=coletar.BANCO, variantes=variantes(c), regimes=("quente", "frio"),
            aquecimentos=a.aquecimentos, repeticoes={"quente": a.n_quente, "frio": a.n_frio}, ordem="ABA", semente=a.semente,
            cache_planos="escopo", estatisticas_tabelas=tuple(c.tabelas), consulta_id=qid, titulo=c.titulo + " [iteracao 2, pos-hoc]")
        res = executar_experimento(cfg, arm, plano, progresso=log)
        arm.db.execute("INSERT INTO coleta_consulta(rodada,query_id,experiment_id,tentativa,status,notas_json) VALUES (?,?,?,1,'concluido',?)",
                       (a.rodada, qid, res["experiment_id"], '["pos-hoc: iteracao 2 do framework"]'))
        arm.db.execute("UPDATE catalog_query SET antipattern_id = ?, selectivity_class = ?, notes = 'posthoc' WHERE query_id = ?", (c.antipadrao, c.seletividade, qid))
        arm.db.commit()
        log(f"[{qid}] iteracao 2 concluida: experimento {res['experiment_id']}")
    arm.fechar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
