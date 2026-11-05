"""CLI: `python -m tcclab ping|medir|exportar|estatistica|relatorio|analisar-indices|prescrever` (ou o comando `tcclab`).

`estatistica` e `relatorio` leem so o SQLite (nunca o SQL Server) e exigem o extra `analise`.
`analisar-indices` e `prescrever` (camadas 2 e 3) sao offline (arquivos .sql, .sqlplan e o JSON de esquema) e exigem o sqlglot (extra `diagnostico`).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import Config
from .driver import conectar
from .runner import PlanoExperimento, Variante, executar_experimento
from .store import Armazem


def _cmd_ping(args, cfg: Config) -> int:
    from . import ambiente
    from .medir import preparar_sessao

    con = conectar(cfg, args.banco)
    try:
        amb = ambiente.capturar_ambiente(con, args.banco)
        saida = {k: amb[k] for k in ("sql_version", "edition", "compat_level", "cpu_count", "physical_memory_mb",
                                    "max_server_memory_mb", "maxdop", "container_platform", "query_store_options",
                                    "driver", "python_version", "database")}
        preparar_sessao(con)
        r = con.executar_lote("SELECT 1 AS ok; ")
        saida["select_1"] = r.conjuntos[0].total_linhas == 1
        saida["avisos"] = amb["avisos"]
        print(json.dumps(saida, ensure_ascii=False, indent=2, default=str))
        if amb["avisos"]:
            print("AVISO: ha atividade/restauracao em andamento; nao confie em medicoes de TEMPO.", file=sys.stderr)
        return 0
    finally:
        con.fechar()


def _cmd_medir(args, cfg: Config) -> int:
    sql = Path(args.arquivo).read_text(encoding="utf-8") if args.arquivo else args.sql
    if not sql:
        print("Informe --sql ou --arquivo", file=sys.stderr)
        return 2
    variantes = [Variante("A", sql, kind="original")]
    if args.ddl_b or args.sql_b:
        variantes.append(Variante("B", args.sql_b or sql, args.ddl_b, args.reverter_b,
                                  kind="index" if args.ddl_b else "rewrite"))
        variantes[0].nome = "antes"
        variantes[1].nome = "depois"
    regimes = ("quente", "frio") if args.regime == "ambos" else (args.regime,)
    plano = PlanoExperimento(
        nome=args.rotulo, database=args.banco, variantes=variantes, regimes=regimes,
        aquecimentos=args.aquecimentos,
        repeticoes={"quente": args.repeticoes, "frio": args.repeticoes_frio or args.repeticoes},
        ordem=args.ordem if len(variantes) > 1 else "AB", semente=args.semente,
        cache_planos=args.cache_planos, estatisticas_tabelas=tuple(args.estatisticas or ()),
        titulo=args.rotulo, microssegundos=not args.sem_us,
    )
    arm = Armazem(args.saida)
    try:
        res = executar_experimento(cfg, arm, plano, progresso=lambda m: print(m, file=sys.stderr))
    finally:
        arm.fechar()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        print(tabela_resumo(res))
    return 0


def tabela_resumo(res: dict) -> str:
    """Tabela compacta: mediana (min-max) por bloco. Leituras logicas sao a metrica principal."""
    cab = f"experimento {res['experiment_id']}  (avisos: {len(res['avisos'])})"
    linhas = [cab, f"{'regime':7} {'bloco':5} {'variante':9} {'n':>3} {'leit.logicas':>14} {'leit.fisicas':>13} {'read-ahead':>11} {'CPU ms':>14} {'decorrido ms':>14} {'CPU us (DMV)':>16}  plano"]
    for r in res["resumo"]:
        f = lambda m: f"{r[m]['mediana']:g} ({r[m]['minimo']:g}-{r[m]['maximo']:g})"  # noqa: E731
        linhas.append(f"{r['regime']:7} {r['bloco']:5} {r['variante']:9} {r['n']:>3} {f('logical_reads'):>14} {f('physical_reads'):>13} {f('read_ahead_reads'):>11} {f('cpu_ms'):>14} {f('elapsed_ms'):>14} {(f('cpu_us') if 'cpu_us' in r else '-'):>16}  {r['query_plan_hash']}")
    return "\n".join(linhas)


def _cmd_exportar(args, cfg: Config) -> int:
    from .exportar import exportar

    arm = Armazem(args.banco_sqlite)
    try:
        for c in exportar(arm, args.experimento, args.saida, args.formato):
            print(c)
    finally:
        arm.fechar()
    return 0


COMANDOS_SO_SQLITE = frozenset({"estatistica", "relatorio", "analisar-indices", "prescrever"})   # offline: nem a configuracao (senha) e carregada
MSG_EXTRA_ANALISE = "A analise exige o extra opcional 'analise' (numpy, scipy, matplotlib): .venv/bin/pip install -e '.[analise]'"


def _carregar_analise(args):
    """Importa a camada de analise (extra opcional) e monta criterios, antipadroes e a analise. Le so o SQLite."""
    try:
        from . import analise
    except ImportError as e:
        raise SystemExit(f"{MSG_EXTRA_ANALISE} ({e})")
    try:
        crit = analise.Criterios.de_arquivo(args.criterios) if args.criterios else analise.Criterios()
        sobrescritas = {k: v for k, v in (("semente", args.semente), ("reamostragens", args.reamostragens)) if v is not None}
        if sobrescritas:
            crit = analise.Criterios.de_dict(crit.como_dict() | sobrescritas)
        lido = lambda arq: json.loads(Path(arq).read_text(encoding="utf-8")) if arq else None  # noqa: E731
        return analise, analise.analisar(args.banco_sqlite, args.experimento, crit, lido(args.antipadroes), lido(args.papeis))
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as e:  # entradas invalidas: mensagem curta, sem traceback
        raise SystemExit(f"ERRO: {e}")


def _cmd_estatistica(args, cfg: Config | None) -> int:
    analise, res = _carregar_analise(args)
    from . import relatorio

    arquivos = relatorio.escrever_csv(res, args.saida) + relatorio.escrever_json(res, args.saida)
    if args.gravar:
        print(f"gravado em analise_execucao (an_id {analise.gravar_no_banco(args.banco_sqlite, res)})", file=sys.stderr)
    print(analise.para_json(res) if args.json else relatorio.relatorio_texto(res))
    for c in arquivos:
        print(c, file=sys.stderr)
    return 0


def _cmd_relatorio(args, cfg: Config | None) -> int:
    analise, res = _carregar_analise(args)
    from . import relatorio

    if not args.sem_figuras:
        try:
            import matplotlib  # noqa: F401
        except ImportError as e:
            raise SystemExit(f"{MSG_EXTRA_ANALISE} ({e})")
    arquivos = relatorio.gerar_relatorio(res, args.saida, figuras=not args.sem_figuras)
    if args.gravar:
        print(f"gravado em analise_execucao (an_id {analise.gravar_no_banco(args.banco_sqlite, res)})", file=sys.stderr)
    print(relatorio.relatorio_texto(res))
    for c in arquivos:
        print(c, file=sys.stderr)
    return 0


def _ler_entrada_offline(args):
    """(sql, plano XML | None, esquema | None) dos arquivos informados; erro de entrada vira mensagem curta."""
    try:
        from . import schema

        sql = Path(args.sql).read_text(encoding="utf-8")
        plano = Path(args.plano).read_text(encoding="utf-8") if args.plano else None
        return sql, plano, (schema.carregar(args.esquema) if args.esquema else None)
    except (OSError, ValueError, KeyError) as e:
        raise SystemExit(f"ERRO: {e}")


def _cmd_analisar_indices(args, cfg: Config | None) -> int:
    from . import analise_indices

    sql, plano, esquema = _ler_entrada_offline(args)
    cols = [c.strip() for c in args.colunas.split(",")] if args.colunas else None
    a = analise_indices.analisar_indices(sql, plano, esquema, colunas_necessarias=cols)
    print(a.para_json() if args.json else analise_indices.formatar(a))
    return 0


def _cmd_prescrever(args, cfg: Config | None) -> int:
    from . import prescricao

    sql, plano, esquema = _ler_entrada_offline(args)
    cols = [c.strip() for c in args.colunas.split(",")] if args.colunas else None
    p = prescricao.prescrever(sql, plano, esquema, cols)
    print(p.para_json() if args.json else prescricao.formatar(p))
    return 0


def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tcclab", description="Nucleo de medicao do TCC (M0)")
    p.add_argument("--versao", action="version", version=f"tcclab {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ping", help="conecta, mostra o ambiente e avisa sobre atividade em segundo plano")
    sp.add_argument("--banco", default=None)

    sm = sub.add_parser("medir", help="mede uma consulta (e opcionalmente antes/depois de um DDL)")
    sm.add_argument("--sql"); sm.add_argument("--arquivo")
    sm.add_argument("--banco", default=None)
    sm.add_argument("--regime", choices=["quente", "frio", "ambos"], default="quente")
    sm.add_argument("--repeticoes", type=int, default=10)
    sm.add_argument("--repeticoes-frio", type=int, default=None, dest="repeticoes_frio")
    sm.add_argument("--aquecimentos", type=int, default=3)
    sm.add_argument("--semente", type=int, default=20261005)
    sm.add_argument("--ordem", choices=["ABA", "AB", "aleatoria"], default="ABA")
    sm.add_argument("--ddl-b", dest="ddl_b", help="DDL aplicado na variante B (ex.: CREATE INDEX ...)")
    sm.add_argument("--reverter-b", dest="reverter_b", help="DDL que desfaz a variante B (ex.: DROP INDEX ...)")
    sm.add_argument("--sql-b", dest="sql_b", help="consulta reescrita (variante B); padrao: mesma consulta")
    sm.add_argument("--estatisticas", action="append", help="tabela para UPDATE STATISTICS ... FULLSCAN (repetivel)")
    sm.add_argument("--cache-planos", choices=["escopo", "global"], default="escopo")
    sm.add_argument("--rotulo", default="medicao")
    sm.add_argument("--sem-us", action="store_true", dest="sem_us", help="nao le last_worker_time/last_elapsed_time (us)")
    sm.add_argument("--json", action="store_true", help="imprime o resumo completo em JSON")
    sm.add_argument("--saida", default="resultados/tcclab.sqlite", help="arquivo SQLite")

    se = sub.add_parser("exportar", help="exporta um experimento para CSV/JSON")
    se.add_argument("--banco-sqlite", default="resultados/tcclab.sqlite", dest="banco_sqlite")
    se.add_argument("--experimento", type=int, required=True)
    se.add_argument("--formato", choices=["csv", "json"], default="csv")
    se.add_argument("--saida", default="resultados/export")

    for nome, ajuda in (("estatistica", "analise estatistica (CSV e JSON) a partir do SQLite; nao conecta ao SQL Server"),
                        ("relatorio", "estatistica + tabelas LaTeX, figuras PDF e numeros do artigo; nao conecta ao SQL Server")):
        sa = sub.add_parser(nome, help=ajuda)
        sa.add_argument("--banco", "--banco-sqlite", default="resultados/tcclab.sqlite", dest="banco_sqlite",
                        help="arquivo SQLite (lido em modo somente leitura)")
        sa.add_argument("--experimento", type=int, nargs="+", default=None,
                        help="um ou mais experiment_id (padrao: todos do arquivo; os descartados por deriva sao excluidos e listados)")
        sa.add_argument("--saida", default="resultados/analise")
        sa.add_argument("--criterios", help="JSON com criterios que sobrescrevem os padroes (ver analise.Criterios)")
        sa.add_argument("--semente", type=int, default=None, help="semente do bootstrap (padrao: a de Criterios)")
        sa.add_argument("--reamostragens", type=int, default=None, help="reamostragens do bootstrap (padrao: 10000)")
        sa.add_argument("--antipadroes", help="JSON {query_id: antipadrao} que prevalece sobre o catalogo")
        sa.add_argument("--papeis", help="JSON {query_id: principal|controle} que prevalece sobre catalog_query.notes")
        sa.add_argument("--gravar", action="store_true",
                        help="grava o resultado na tabela auxiliar analise_execucao do proprio SQLite (escreve no arquivo)")
        if nome == "estatistica":
            sa.add_argument("--json", action="store_true", help="imprime o resultado completo em JSON")
        else:
            sa.add_argument("--sem-figuras", action="store_true", dest="sem_figuras", help="nao gera os PDFs (nao importa matplotlib)")
    for nome, ajuda in (("analisar-indices", "camada 2: causa da falta de desempenho por tabela (indice ausente, nao sargavel, key lookup...); offline"),
                        ("prescrever", "camada 3: DDL de indice e CANDIDATAS de reescrita (nao verificadas); offline, nao conecta ao SQL Server")):
        so = sub.add_parser(nome, help=ajuda)
        so.add_argument("--sql", required=True, help="arquivo com a consulta T-SQL")
        so.add_argument("--plano", help="arquivo .sqlplan (real ou estimado)")
        so.add_argument("--esquema", help="JSON de metadados (tcclab.schema; ex.: experimento/schema_so2010.json)")
        so.add_argument("--colunas", help="SELECT *: colunas de que a aplicacao precisa, separadas por virgula")
        so.add_argument("--json", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    cfg = None  # estatistica e relatorio nao usam o SQL Server: nem a configuracao (senha) e carregada
    if args.cmd not in COMANDOS_SO_SQLITE:
        cfg = Config.carregar(database=getattr(args, "banco", None))
        if hasattr(args, "banco") and args.banco is None:
            args.banco = cfg.database
    try:
        return {"ping": _cmd_ping, "medir": _cmd_medir, "exportar": _cmd_exportar,
                "estatistica": _cmd_estatistica, "relatorio": _cmd_relatorio,
                "analisar-indices": _cmd_analisar_indices, "prescrever": _cmd_prescrever}[args.cmd](args, cfg)
    except PermissionError as e:
        print(f"RECUSADO: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
