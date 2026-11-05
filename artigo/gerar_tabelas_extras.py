#!/usr/bin/env python3
"""Gera tabelas LaTeX adicionais a partir dos CSV da analise (artigo/dados/<nome>/csv/), sem digitar numero a mao.

  tab_bracos_quente.tex    leituras logicas medianas por consulta e braco (cenario quente)
  tab_escrita_indices.tex  custo de escrita por indice distinto (razao com/sem indice; mediana entre consultas que o usam)
Uso: ./gerar_tabelas_extras.py <nome>       (le dados/<nome>/csv, escreve dados/<nome>/tabelas)
"""
from __future__ import annotations

import csv
import statistics as st
import sys
from pathlib import Path


def pt(x: float, casas: int = 0) -> str:
    s = f"{x:,.{casas}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def razao(x: float) -> str:
    if x >= 100:
        return pt(x, 0)
    if x >= 10:
        return pt(x, 1)
    return pt(x, 2)


def ler(p: Path) -> list[dict]:
    return list(csv.DictReader(p.open(encoding="utf-8")))


def bracos(csvdir: Path, regime: str = "quente") -> str:
    comp = [r for r in ler(csvdir / "comparisons.csv") if r["regime"] == regime and r["metrica"] == "logical_reads"]
    por: dict[str, dict] = {}
    for r in comp:
        d = por.setdefault(r["query_id"], {"papel": r["papel"], "ap": r["antipadrao"]})
        d["original"] = float(r["mediana_antes"]) if r["comparacao"] != "nativo_vs_prescricao" else d.get("original")
        if r["comparacao"] == "nativo_vs_prescricao":
            continue
        chave = {"reescrita": "reescrita", "indice": "indice", "reescrita+indice": "prescricao", "nativo": "nativo"}.get(r["tratamento"])
        if r["tratamento_kind"] == "prescricao" or r["comparacao"] == "principal":
            chave = "prescricao"
        if chave:
            d[chave] = float(r["mediana_depois"])
    ordem = sorted(por, key=lambda q: (por[q]["ap"], int("".join(c for c in q if c.isdigit()))))
    L = [r"{\footnotesize\setlength{\tabcolsep}{3pt}", r"\begin{tabular}{llrrrrr}", r"\toprule",
         r"Consulta & Classe & Original & Só reescrita & Só índice & Prescrição & Nativo \\", r"\midrule"]
    for q in ordem:
        d = por[q]
        cel = lambda k: pt(d[k]) if k in d and d[k] is not None else "--"  # noqa: E731
        nome = q + (r"$^{*}$" if d["papel"] == "controle" else "")
        L.append(f"{nome} & {d['ap']} & {cel('original')} & {cel('reescrita')} & {cel('indice')} & {cel('prescricao')} & {cel('nativo')} \\\\")
    L += [r"\bottomrule", r"\end{tabular}\par", r"\vspace{3pt}", r"\begin{minipage}{\linewidth}\footnotesize",
          r"Mediana das leituras lógicas, cenário " + regime + r". Prescrição = reescrita com índice (classes 1 a 3) ou só o índice (classe 4). \texttt{--}: braço inexistente ou sem sugestão distinta do índice do \textit{framework}. $^{*}$ Consulta de controle.",
          r"\end{minipage}}"]
    return "\n".join(L) + "\n"


def escrita(csvdir: Path) -> str:
    rows = ler(csvdir / "escrita.csv")
    por: dict[str, dict] = {}
    for r in rows:
        d = por.setdefault(r["indice"], {"tabela": r["tabela"].replace("dbo.", ""), "pag": r["indice_paginas"], "cria": r["criacao_ms"], "q": set(), "v": {}})
        d["q"].add(r["query_id"])
        if r["metrica"] in ("logical_reads", "log_bytes"):
            d["v"].setdefault((r["operacao"], r["metrica"]), []).append(float(r["razao"]))
    L = [r"{\footnotesize\setlength{\tabcolsep}{3pt}", r"\begin{tabular}{llrrrrrrr}", r"\toprule",
         r" &  &  & \multicolumn{3}{c}{Leituras lógicas} & \multicolumn{3}{c}{Bytes de log} \\", r"\cmidrule(lr){4-6}\cmidrule(lr){7-9}",
         r"Índice & Tabela & Págs. & \texttt{INS} & \texttt{UPD} & \texttt{DEL} & \texttt{INS} & \texttt{UPD} & \texttt{DEL} \\", r"\midrule"]
    for ix in sorted(por, key=lambda k: (por[k]["tabela"], k)):
        d = por[ix]
        g = lambda op, m: razao(st.median(d["v"][(op, m)])) if (op, m) in d["v"] else "--"  # noqa: E731
        nome = ix.replace("ix_exp_", "").replace("_", r"\_")
        L.append(f"{nome} & {d['tabela']} & {pt(float(d['pag']))} & {g('insert','logical_reads')} & {g('update','logical_reads')} & {g('delete','logical_reads')} & {g('insert','log_bytes')} & {g('update','log_bytes')} & {g('delete','log_bytes')} \\\\")
    L += [r"\bottomrule", r"\end{tabular}\par", r"\vspace{3pt}", r"\begin{minipage}{\linewidth}\footnotesize",
          r"Razão com/sem o índice, mediana das repetições de 1.000 linhas por operação (\texttt{INS} = \texttt{INSERT}, \texttt{UPD} = \texttt{UPDATE}, \texttt{DEL} = \texttt{DELETE}), dentro de transação desfeita; quando o mesmo índice foi medido por mais de uma consulta, mediana entre as consultas. Sem limiar de aceitação: o custo é reportado, não julgado.",
          r"\end{minipage}}"]
    return "\n".join(L) + "\n"


def _rotulo_indice(create: str, origem: str) -> str:
    """Tabela(chave) +INCLUDE resumido; distingue indices de mesmo nome com colunas incluidas diferentes."""
    import re
    m = re.search(r"ON\s+(\S+)\s*\(([^)]*)\)(?:\s*INCLUDE\s*\(([^)]*)\))?", create, re.IGNORECASE)
    tab = m.group(1).replace("[", "").replace("]", "").replace("dbo.", "")
    chave = ", ".join(x.strip().strip("[]") for x in m.group(2).split(","))
    inc = [x.strip().strip("[]") for x in (m.group(3) or "").split(",") if x.strip()]
    r = f"{tab}({chave})"
    if inc:
        r += f" +{', '.join(inc)}" if len(inc) <= 3 else f" +{len(inc)} colunas"
    return r + (r"$^{\dagger}$" if origem == "nativo" else "")


def escrita_sqlite(sqlite_path: Path) -> str:
    import sqlite3
    db = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT rowid rid, * FROM escrita_medicao ORDER BY rowid").fetchall()
    origens = [dict(r) for r in db.execute("SELECT rowid rid, * FROM escrita_origem ORDER BY rowid")]
    indices = {r["indice"]: dict(r) for r in db.execute("SELECT * FROM escrita_indice")}
    blocos, sem, com = [], [], []
    for r in rows:  # cada indice: bloco 'sem indice' seguido do bloco do proprio indice
        if r["indice"] == "sem indice":
            if com:
                blocos.append((sem, com)); sem, com = [], []
            sem.append(r)
        else:
            com.append(r)
    if com:
        blocos.append((sem, com))
    assert len(blocos) == len(origens), (len(blocos), len(origens))
    med = lambda L, op, m: st.median([x[m] for x in L if x["operacao"] == op])  # noqa: E731
    linhas = []
    for (sem_, com_), o in zip(blocos, origens):
        ind = indices[com_[0]["indice"]] if com_[0]["indice"] in indices else {}
        pag = next(float(v["paginas"]) for k, v in indices.items() if k == com_[0]["indice"]) if ind else 0.0
        v = {(op, m): med(com_, op, m) / med(sem_, op, m) for op in ("insert", "update", "delete") for m in ("logical_reads", "log_bytes")}
        linhas.append((o["origem"], _rotulo_indice(o["create_ddl"], o["origem"]), pag, v))
    # paginas por rotulo original (escrita_indice tem uma linha por indice medido, na mesma ordem das origens)
    pags = [float(r["paginas"]) for r in db.execute("SELECT paginas FROM escrita_indice ORDER BY rowid")]
    L = [r"{\footnotesize\setlength{\tabcolsep}{2.5pt}", r"\begin{tabular}{lrrrrrrr}", r"\toprule",
         r" &  & \multicolumn{3}{c}{Leituras lógicas} & \multicolumn{3}{c}{Bytes de log} \\", r"\cmidrule(lr){3-5}\cmidrule(lr){6-8}",
         r"Índice & Págs. & \texttt{INS} & \texttt{UPD} & \texttt{DEL} & \texttt{INS} & \texttt{UPD} & \texttt{DEL} \\", r"\midrule"]
    ordenado = sorted(zip(linhas, pags), key=lambda t: (t[0][1].split("(")[0], t[0][0] == "nativo", t[0][1]))
    for (orig, rot, _, v), pag in ordenado:
        L.append(f"{rot} & {pt(pag)} & {razao(v[('insert','logical_reads')])} & {razao(v[('update','logical_reads')])} & {razao(v[('delete','logical_reads')])} & "
                 f"{razao(v[('insert','log_bytes')])} & {razao(v[('update','log_bytes')])} & {razao(v[('delete','log_bytes')])} \\\\")
    L += [r"\bottomrule", r"\end{tabular}\par", r"\vspace{3pt}", r"\begin{minipage}{\linewidth}\footnotesize",
          r"Razão com/sem o índice, mediana de 10 repetições de 1.000 linhas por operação (\texttt{INS} = \texttt{INSERT}, \texttt{UPD} = \texttt{UPDATE} da coluna-chave, \texttt{DEL} = \texttt{DELETE}), dentro de transação desfeita, nas mesmas linhas com e sem o índice. $^{\dagger}$ Índice sugerido pelo recurso nativo. Sem limiar de aceitação: o custo é reportado, não julgado.",
          r"\end{minipage}}"]
    return "\n".join(L) + "\n"


def main() -> int:
    nome = sys.argv[1] if len(sys.argv) > 1 else "r1"
    base = Path(__file__).resolve().parent / "dados" / nome
    cd, td = base / "csv", base / "tabelas"
    td.mkdir(parents=True, exist_ok=True)
    (td / "tab_bracos_quente.tex").write_text("% gerado por gerar_tabelas_extras.py; nao editar a mao\n" + bracos(cd), encoding="utf-8")
    if len(sys.argv) > 2:  # SQLite da rodada dedicada de escrita (substitui o csv da R1/R2)
        (td / "tab_escrita_indices.tex").write_text("% gerado por gerar_tabelas_extras.py a partir de " + Path(sys.argv[2]).parent.name + "; nao editar a mao\n" + escrita_sqlite(Path(sys.argv[2])), encoding="utf-8")
    else:
        (td / "tab_escrita_indices.tex").write_text("% gerado por gerar_tabelas_extras.py; nao editar a mao\n" + escrita(cd), encoding="utf-8")
    print("geradas em", td)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
