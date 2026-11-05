#!/usr/bin/env python
"""Avaliacao OFFLINE da Prescricao (camadas 2 e 3): indice e reescrita gerados x os do catalogo (escritos a mao pelo autor).

LIMITACAO OBRIGATORIA (repetida no relatorio): as regras de tcclab.analise_indices / tcclab.prescricao foram derivadas dos
MESMOS casos do catalogo (e calibradas em parte com as medicoes da R1 desses casos). A concordancia aqui e um TESTE DE REGRESSAO
e de coerencia catalogo x gerador; NAO prova generalizacao. Divergencias sao listadas, nao escondidas. O conjunto heldout.toml e
reservado a validacao do Diagnostico e NAO e usado aqui.

Para cada consulta de catalogo.toml com planos em planos/<ID>/original.{estimado,real}.sqlplan roda `prescrever` em cenarios
(plano real, plano estimado, so SQL, regra literal de seletividade, e para AP3 tambem sem informar as colunas) e compara:
  indice: com o `indice` do catalogo (no cenario "sem colunas" de AP3, com `indice_original`):
      igual        mesma tabela, mesma chave na mesma ordem, mesmo conjunto de INCLUDE
      equivalente  mesma chave; INCLUDE difere so por colunas que o indice ja carrega (chave ou chave do clusterizado)
      diferente    com o porque (nao propoe, chave diferente, INCLUDE diferente, outra tabela)
      sem prescricao  nada foi gerado para a tabela
  reescrita: a candidata final com a `sql_reescrita` do catalogo, apos normalizar as duas com o sqlglot:
      igual        mesmo texto normalizado (caixa, espacos, ordem dos conjuntos de AND, JOIN = INNER JOIN)
      equivalente  igual depois de tolerar diferencas de uma lista FECHADA e documentada: caixa dos literais de texto (colacao
                   insensivel), ordem dos lados de uma igualdade
      diferente    com o texto normalizado de ambas
      sem prescricao  a ferramenta nao gerou reescrita (e o catalogo tem, ou nenhuma tem)
Nenhuma reescrita e dada como verificada aqui. A verificacao de resultado contra o banco so roda com --verificar-no-banco
(SELECTs de leitura; o banco precisa estar LIVRE: o script recusa se detectar a coleta em andamento).

Saidas em experimento/resultados/prescricao/: prescricao_casos.csv, prescricao_relatorio.md (e prescricao_estimativas.csv com --medidos).
Uso (em pratica/nucleo):
  .venv/bin/python ../experimento/avaliar_prescricao.py
  .venv/bin/python ../experimento/avaliar_prescricao.py --medidos CAMINHO/tcclab.sqlite      # compara tamanhos e leituras estimados com os medidos (copie o SQLite antes)
  .venv/bin/python ../experimento/avaliar_prescricao.py --verificar-no-banco                   # SO com o banco livre; conecta e executa SELECTs
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sqlite3
import statistics
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from tcclab import analise_indices, catalogo, schema
from tcclab import prescricao as prescricao_mod
from tcclab.analise_indices import LIMIARES_PADRAO, estimar_impacto_escrita
from tcclab.prescricao import ConfigPrescricao, Prescricao, prescrever, verificar_candidatas

import sqlglot
from sqlglot import exp

AQUI = Path(__file__).resolve().parent
CENARIOS = []   # preenchido abaixo


@dataclass(frozen=True)
class Cenario:
    nome: str
    plano: str            # real | estimado | sql
    literal: bool = False # regra literal de seletividade (so o limiar)
    colunas: bool = True  # AP3: informar as colunas da reescrita do catalogo


CENARIOS = [
    Cenario("real", "real"),
    Cenario("estimado", "estimado"),
    Cenario("so_sql", "sql"),
    Cenario("real_regra_literal", "real", literal=True),
    Cenario("real_sem_colunas", "real", colunas=False),
]


# ----------------------------------------------------------------------------------------------- comparacao de indices
def _sig_gerado(esq: schema.EsquemaBanco, tabela: str, chave: list[str], incl: list[str]) -> tuple:
    return tabela.lower(), tuple(c.lower() for c in chave), frozenset(c.lower() for c in incl)


def _efetivo(esq: schema.EsquemaBanco, tabela: str, chave: tuple, incl: frozenset) -> frozenset:
    clust = esq.indice_clusterizado(tabela)
    ja = set(chave) | {c.lower() for c in (clust.chave if clust else ())}
    return frozenset(c for c in incl if c not in ja)


def comparar_indice(p: Prescricao, ref_ddl: str | None, esq: schema.EsquemaBanco) -> tuple[str, str, str, str]:
    """(classe, motivo, ddl gerado, indices adicionais) do indice gerado para a tabela do indice do catalogo."""
    if ref_ddl is None:
        gerados = [i for i in p.indices if i.decisao == "propor"]
        return ("sem prescrição" if not gerados else "diferente"), ("catálogo sem índice" + ("" if not gerados else "; a ferramenta propõe " + "; ".join(i.ddl for i in gerados))), \
            "; ".join(i.ddl for i in gerados), ""
    tab, chave, incl = catalogo._assinatura_indice(ref_ddl)
    simples = tab.split(".")[-1]
    propostos = [i for i in p.indices if i.decisao == "propor"]
    da_tabela = [i for i in propostos if i.tabela.lower() == simples]
    outros = [i for i in propostos if i not in da_tabela]
    adicionais = "; ".join(i.ddl for i in outros)
    mesmo = next((i for i in da_tabela if tuple(c.lower() for c in i.colunas_chave) == chave), da_tabela[0] if da_tabela else None)
    extras_mesma_tabela = [i for i in da_tabela if i is not mesmo]
    if extras_mesma_tabela:
        adicionais = "; ".join([i.ddl for i in extras_mesma_tabela] + ([adicionais] if adicionais else []))
    if mesmo is None:
        nao = [i for i in p.indices if i.decisao == "nao_propor" and i.tabela.lower() == simples]
        if nao:
            return "diferente", f"a ferramenta recomenda NÃO indexar {nao[0].tabela}: {nao[0].justificativa}", "", adicionais
        return "sem prescrição", "nenhuma prescrição de índice para a tabela", "", adicionais
    sg = _sig_gerado(esq, mesmo.tabela, mesmo.colunas_chave, mesmo.incluidas)
    if sg[1] == chave and sg[2] == incl:
        return "igual", "", mesmo.ddl, adicionais
    if sg[1] == chave and _efetivo(esq, simples, chave, sg[2]) == _efetivo(esq, simples, chave, incl):
        return "equivalente", "INCLUDE difere só por colunas que o índice já carrega (chave ou chave do clusterizado)", mesmo.ddl, adicionais
    if sg[1] != chave:
        extra = " (mesmo conjunto de colunas, ordem diferente)" if set(sg[1]) == set(chave) else ""
        return "diferente", f"chave gerada ({', '.join(sg[1])}) x catálogo ({', '.join(chave)}){extra}", mesmo.ddl, adicionais
    mais, menos = sorted(sg[2] - incl), sorted(incl - sg[2])
    return "diferente", "INCLUDE: " + "; ".join(x for x in (f"a mais na ferramenta {mais}" if mais else "", f"a menos na ferramenta {menos}" if menos else "") if x), mesmo.ddl, adicionais


# ----------------------------------------------------------------------------------------------- comparacao de reescritas
def _canon(sql: str, nivel: int) -> str:
    """Forma normal do SQL. nivel 1: caixa dos identificadores, ordem dos AND, JOIN = INNER JOIN. nivel 2: tambem caixa dos
    literais de texto e ordem dos lados de uma igualdade (diferencas toleradas, lista FECHADA)."""
    t = sqlglot.parse_one(sql, read="tsql")
    t = t.transform(lambda n: exp.to_identifier(n.name.lower()) if isinstance(n, exp.Identifier) and not n.quoted else n)
    if nivel >= 2:
        t = t.transform(lambda n: exp.Literal.string(n.this.lower()) if isinstance(n, exp.Literal) and n.is_string else n)
        t = t.transform(lambda n: exp.EQ(this=min(n.this, n.expression, key=lambda x: x.sql()), expression=max(n.this, n.expression, key=lambda x: x.sql()))
                        if isinstance(n, exp.EQ) else n)

    def conjuntos(no):
        while isinstance(no, exp.Paren):
            no = no.this
        if isinstance(no, exp.And):
            yield from conjuntos(no.this)
            yield from conjuntos(no.expression)
        else:
            yield no

    for holder in list(t.find_all(exp.Where, exp.Having)):
        lista = sorted(conjuntos(holder.this), key=lambda x: x.sql())
        holder.set("this", exp.and_(*[x.copy() for x in lista], copy=False))
    for j in t.find_all(exp.Join):
        if j.args.get("on") is not None:
            lista = sorted(conjuntos(j.args["on"]), key=lambda x: x.sql())
            j.set("on", exp.and_(*[x.copy() for x in lista], copy=False))
        if str(j.args.get("kind") or "").upper() == "INNER":
            j.set("kind", None)
    return t.sql(dialect="tsql")


def comparar_reescrita(p: Prescricao, ref_sql: str | None) -> tuple[str, str, str]:
    """(classe, motivo, regras aplicadas)."""
    final = p.reescrita_final
    regras = "+".join(final.regras_aplicadas) if final else ""
    if final is None:
        if ref_sql:
            return "sem prescrição", "a ferramenta não gerou reescrita; " + ("; ".join(r.justificativa for r in p.recomendacoes if r.regra.startswith("RW")) or "sem recomendação em texto"), regras
        return "sem prescrição", "nem a ferramenta nem o catálogo têm reescrita", regras
    if not ref_sql:
        return "diferente", "a ferramenta gera reescrita e o catálogo não tem", regras
    try:
        a1, b1 = _canon(final.sql_reescrito, 1), _canon(ref_sql, 1)
        if a1 == b1:
            return "igual", "", regras
        a2, b2 = _canon(final.sql_reescrito, 2), _canon(ref_sql, 2)
        if a2 == b2:
            return "equivalente", "difere só por caixa de literal de texto ou ordem dos lados de uma igualdade (colação insensível)", regras
    except Exception as e:   # SQL do catalogo que o sqlglot nao normaliza: nao classifica como igual
        return "diferente", f"não foi possível normalizar para comparar: {e}", regras
    return "diferente", f"gerado: {a1} | catálogo: {b1}", regras


# ----------------------------------------------------------------------------------------------- medidos (opcional)
def ler_medidos(caminho: Path) -> dict:
    """Do SQLite de uma rodada (copia): tamanho medido dos indices e mediana de leituras logicas (quente) por consulta e variante."""
    c = sqlite3.connect(f"file:{caminho}?mode=ro&immutable=1", uri=True)
    rodada = c.execute("select rodada from rodada").fetchone()[0]
    tam = {q: (nome, pag) for q, nome, pag in c.execute("select query_id, indice, paginas from escrita_indice")}
    exps = {q: e for q, e in c.execute("select query_id, experiment_id from coleta_consulta where descartado_por_deriva = 0 and status = 'concluido'")}
    leituras: dict[tuple[str, str], tuple[float, int]] = {}
    for q, nome, eid in [(q, None, e) for q, e in exps.items()]:
        linhas = c.execute("select v.nome, m.logical_reads, r.experiment_id from measurement m join run r using(run_id) join variant v using(variant_id) "
                           "where r.experiment_id = ? and r.regime = 'quente' and m.phase = 'measured' and r.status = 'concluido'", (eid,)).fetchall()
        por: dict[str, list[int]] = {}
        for v, lr, _ in linhas:
            por.setdefault(v, []).append(lr)
        for v, lst in por.items():
            leituras[(q, v)] = (statistics.median(lst), eid)
    return {"rodada": rodada, "tamanhos": tam, "leituras": leituras}


# ----------------------------------------------------------------------------------------------- execucao
def coleta_em_andamento() -> bool:
    """Ha um processo de coleta (coletar.py) rodando? A verificacao no banco nao pode concorrer com ela."""
    try:
        r = subprocess.run(["pgrep", "-f", "experimento/coletar.py|coletar.py --rodada"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return True   # sem como saber: assume ocupado
    return bool(r.stdout.strip())


def colunas_do_catalogo(c: catalogo.Consulta) -> list[str] | None:
    """Colunas da reescrita do catalogo (RW-03). CIRCULAR: o catalogo e quem diz quais colunas a aplicacao usa; no mundo real o chamador informa."""
    if c.antipadrao != "AP3" or not c.sql_reescrita:
        return None
    m = re.match(r"SELECT (.*?) FROM", c.sql_reescrita, re.S | re.I)
    return [x.strip() for x in m.group(1).split(",")] if m else None


def rodar(args) -> int:
    cat = catalogo.carregar(args.catalogo)
    esq = schema.carregar(args.esquema)
    medidos = ler_medidos(Path(args.medidos)) if args.medidos else None
    linhas: list[dict] = []
    prescricoes: dict[tuple[str, str], Prescricao] = {}
    ausentes: list[str] = []
    for c in cat.consultas:
        for cen in CENARIOS:
            if cen.nome == "real_sem_colunas" and c.antipadrao != "AP3":
                continue
            xml = None
            if cen.plano != "sql":
                arq = Path(args.planos) / c.id / f"original.{cen.plano}.sqlplan"
                if not arq.exists():
                    ausentes.append(f"{c.id} ({cen.plano})")
                    continue
                xml = arq.read_text(encoding="utf-8")
            lim = replace(LIMIARES_PADRAO, regra_literal_seletividade=cen.literal)
            cols = colunas_do_catalogo(c) if cen.colunas else None
            p = prescrever(c.sql_original, xml, esq, cols, ConfigPrescricao(limiares=lim))
            prescricoes[(c.id, cen.nome)] = p
            ref_idx = (c.indice_original or c.indice) if cen.nome == "real_sem_colunas" else c.indice
            ref_rw = None if cen.nome == "real_sem_colunas" else c.sql_reescrita
            ci, mi, ddl, adic = comparar_indice(p, ref_idx, esq)
            cr, mr, regras = comparar_reescrita(p, ref_rw)
            causas = "; ".join(f"{t.tabela}:{'/'.join(x.codigo for x in t.causas)}" for t in p.analise.tabelas)
            prop = next((i for i in p.indices if i.decisao == "propor" and ddl and i.ddl == ddl), None)
            linhas.append({
                "consulta": c.id, "antipadrao": c.antipadrao, "papel": c.papel, "cenario": cen.nome,
                "indice_catalogo": ref_idx or "", "indice_gerado": ddl, "indice_classe": ci, "indice_motivo": mi, "indices_adicionais": adic,
                "reescrita_regras": regras, "reescrita_classe": cr, "reescrita_motivo": mr, "causas": causas,
                "leituras_varredura": round(prop.leituras_antes) if prop and prop.leituras_antes else "",
                "leituras_depois_estimadas": round(prop.leituras_depois) if prop and prop.leituras_depois is not None else "",
                "paginas_indice_estimadas": prop.impacto_escrita.paginas_estimadas if prop and prop.impacto_escrita else "",
                "avisos": " | ".join(p.avisos)[:300], "verificacao_no_banco": "não executada",
            })
    verif: list[dict] = []
    if args.verificar_no_banco:
        verif = verificar_no_banco(args, cat, prescricoes, linhas)
    saida = Path(args.saida)
    saida.mkdir(parents=True, exist_ok=True)
    with open(saida / "prescricao_casos.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        w.writeheader()
        w.writerows(linhas)
    est = estimativas(cat, esq, prescricoes, medidos) if medidos else []
    if est:
        with open(saida / "prescricao_estimativas.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(est[0].keys()))
            w.writeheader()
            w.writerows(est)
    (saida / "prescricao_relatorio.md").write_text(relatorio(args, cat, linhas, prescricoes, est, medidos, verif, ausentes), encoding="utf-8")
    print(f"{len(linhas)} casos em {saida}")
    return 0


def estimativas(cat, esq, prescricoes, medidos) -> list[dict]:
    """Tamanho estimado x medido dos indices DO CATALOGO (R1) e leituras estimadas x medidas dos gerados que saem `iguais`."""
    out = []
    for c in cat.consultas:
        tam = medidos["tamanhos"].get(c.id)
        if tam and c.indice:
            tab, chave, incl = catalogo._assinatura_indice(c.indice)
            cols_chave = [x for x in chave]
            nomes = {cc.coluna.lower(): cc.coluna for cc in esq.colunas_da(tab)}
            est = estimar_impacto_escrita(esq, tab, [nomes.get(x, x) for x in cols_chave], [nomes.get(x, x) for x in sorted(incl)])
            out.append({"tipo": "tamanho_indice_paginas", "consulta": c.id, "indice": tam[0], "estimado": est.paginas_estimadas, "medido": tam[1],
                        "erro_relativo": round((est.paginas_estimadas - tam[1]) / tam[1], 4) if est.paginas_estimadas and tam[1] else "",
                        "colunas_variaveis": any(esq.coluna(tab, x) and esq.coluna(tab, x).tipo.lower() in ("nvarchar", "varchar") for x in list(chave) + list(incl)),
                        "origem": f"{medidos['rodada']}/escrita_indice"})
        p = prescricoes.get((c.id, "real"))
        if p is None or len(c.tabelas) != 1:
            continue
        prop = next((i for i in p.indices if i.decisao == "propor" and i.leituras_depois is not None), None)
        if prop is None:
            continue
        variante = "reescrita+indice" if c.sql_reescrita else "indice"
        m = medidos["leituras"].get((c.id, variante))
        if m:
            out.append({"tipo": "leituras_logicas", "consulta": c.id, "indice": prop.nome, "estimado": round(prop.leituras_depois), "medido": m[0],
                        "erro_relativo": round((prop.leituras_depois - m[0]) / m[0], 4) if m[0] else "", "colunas_variaveis": "",
                        "origem": f"{medidos['rodada']} experiment_id {m[1]} variante {variante} (índice gerado ≠ necessariamente o do catálogo)"})
    return out


def verificar_no_banco(args, cat, prescricoes, linhas) -> list[dict]:
    if coleta_em_andamento() and not args.mesmo_com_coleta_em_andamento:
        print("RECUSADO: há uma coleta (coletar.py) em andamento; ela exige o banco com exclusividade. Aguarde o fim ou use --mesmo-com-coleta-em-andamento por sua conta e risco.", file=sys.stderr)
        raise SystemExit(3)
    from tcclab.config import Config
    from tcclab.driver import conectar

    cfg = Config.carregar()
    con = conectar(cfg, args.banco)
    out = []
    try:
        for c in cat.consultas:
            p = prescricoes.get((c.id, "real"))
            if p is None or not (p.reescritas or p.reescrita_final):
                continue
            verificar_candidatas(con, p, c.sql_original)
            for cand in p.reescritas + ([p.reescrita_final] if p.reescrita_final not in p.reescritas else []):
                out.append({"consulta": c.id, "regra": cand.regra, "status": cand.status, "linhas": (cand.verificacao or {}).get("linhas", ""),
                            "detalhe": (cand.verificacao or {}).get("erro", "")})
            for l in linhas:
                if l["consulta"] == c.id and l["cenario"] == "real" and p.reescrita_final:
                    l["verificacao_no_banco"] = p.reescrita_final.status
    finally:
        con.fechar()
    return out


# ----------------------------------------------------------------------------------------------- relatorio
def _contagem(linhas, cenario: str, campo: str) -> dict[str, int]:
    r: dict[str, int] = {}
    for l in linhas:
        if l["cenario"] == cenario:
            r[l[campo]] = r.get(l[campo], 0) + 1
    return r


def relatorio(args, cat, linhas, prescricoes, est, medidos, verif, ausentes) -> str:
    L = []
    L.append("# Avaliação offline da Prescrição (camadas ② e ③)\n")
    L.append(f"> Gerado em {date.today().strftime('%d/%m/%Y')} por `experimento/avaliar_prescricao.py`. Offline: nenhuma conexão ao SQL Server. "
             "Planos: `experimento/planos/<ID>/original.{estimado,real}.sqlplan` (linha de base, sem índices não clusterizados, compat. 160, MAXDOP 1). "
             "Esquema: `experimento/schema_so2010.json` (só PKs clusterizadas; sem densidades de estatística).\n")
    L.append("Proveniência (SHA-256, 12 primeiros caracteres): " + "; ".join(
        f"`{n}` {hashlib.sha256(Path(c).read_bytes()).hexdigest()[:12]}" for n, c in (
            ("analise_indices.py", Path(analise_indices.__file__)), ("prescricao.py", Path(prescricao_mod.__file__)), ("schema.py", Path(schema.__file__)),
            ("catalogo.toml", args.catalogo), ("schema_so2010.json", args.esquema))) + f"; sqlglot {sqlglot.__version__}.\n")
    L.append("## LIMITAÇÃO (leia antes de citar qualquer número)\n")
    L.append("As regras da análise e da prescrição foram **derivadas dos mesmos casos do catálogo** que aparecem aqui (e, em parte, calibradas com as medições da R1 "
             "desses casos: o custo de 4 páginas por Key Lookup e o modelo de tamanho do índice). A concordância abaixo é, portanto, **teste de regressão e de coerência "
             "entre o catálogo (escrito à mão pelo autor) e o gerador**, e **não prova de generalização**. O conjunto `heldout.toml` é reservado à validação do "
             "Diagnóstico e não foi usado. Divergências estão listadas na seção 4, sem filtro. Para as consultas AP3 o cenário principal **informa ao gerador as colunas da "
             "reescrita do catálogo** (as colunas necessárias só o autor conhece): a concordância de INCLUDE e de RW-03 nesses casos é parcialmente circular.\n")
    L.append("**Nenhuma reescrita é dada como verificada neste relatório.** " + (
        "A verificação de resultado contra o banco **foi executada** (seção 6)." if verif else
        "A verificação de equivalência de resultado contra o banco **não foi executada** (o banco estava ocupado pela coleta). Comando na seção 6."))
    L.append("")
    L.append("## 1. Resumo por cenário\n")
    L.append("Cenários: `real` = plano real; `estimado` = plano estimado; `so_sql` = sem plano; `real_regra_literal` = plano real e só o limiar de seletividade "
             f"({LIMIARES_PADRAO.sel_baixa:.0%}); `real_sem_colunas` = AP3 sem informar as colunas (índice comparado ao `indice_original` do catálogo, sem reescrita).\n")
    L.append("| cenário | índice: igual | equivalente | diferente | sem prescrição | reescrita: igual | equivalente | diferente | sem prescrição |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for cen in CENARIOS:
        ci, cr = _contagem(linhas, cen.nome, "indice_classe"), _contagem(linhas, cen.nome, "reescrita_classe")
        if not ci and not cr:
            continue
        L.append(f"| {cen.nome} | " + " | ".join(str(ci.get(k, 0)) for k in ("igual", "equivalente", "diferente", "sem prescrição")) + " | " +
                 " | ".join(str(cr.get(k, 0)) for k in ("igual", "equivalente", "diferente", "sem prescrição")) + " |")
    L.append("\nTotais por cenário contam consultas do catálogo (19; `real_sem_colunas` só as 4 de AP3). \"Sem prescrição\" na reescrita inclui as 5 consultas AP4, que não têm reescrita no catálogo nem na ferramenta.\n")
    if ausentes:
        L.append(f"Planos ausentes (cenário não executado): {', '.join(ausentes)}.\n")
    L.append("## 2. Consulta a consulta (cenário `real`)\n")
    L.append("| consulta | AP | papel | índice | reescrita | causas (camada ②) | observação |")
    L.append("|---|---|---|---|---|---|---|")
    for l in linhas:
        if l["cenario"] != "real":
            continue
        obs = []
        if l["indice_motivo"]:
            obs.append("índice: " + l["indice_motivo"])
        if l["indices_adicionais"]:
            obs.append("adicional: " + l["indices_adicionais"])
        if l["reescrita_motivo"] and l["reescrita_classe"] != "igual":
            obs.append("reescrita: " + l["reescrita_motivo"][:160])
        L.append(f"| {l['consulta']} | {l['antipadrao']} | {l['papel']} | {l['indice_classe']} | {l['reescrita_classe']} ({l['reescrita_regras'] or '-'}) | {l['causas']} | {'; '.join(obs).replace('|', '/')} |")
    L.append("")
    L.append("## 3. Diferenças entre cenários\n")
    L.append("Consultas cuja classe de índice muda conforme o cenário (cada linha mostra o porquê):\n")
    mudou = False
    por_consulta: dict[str, dict[str, dict]] = {}
    for l in linhas:
        por_consulta.setdefault(l["consulta"], {})[l["cenario"]] = l
    for qid, cs in por_consulta.items():
        classes = {n: x["indice_classe"] for n, x in cs.items() if n != "real_sem_colunas"}
        if len(set(classes.values())) > 1:
            mudou = True
            L.append(f"- **{qid}**: " + "; ".join(f"`{n}` = {k}" + (f" ({cs[n]['indice_motivo'][:140]})" if cs[n]["indice_motivo"] else "") for n, k in classes.items()))
    if not mudou:
        L.append("- nenhuma")
    L.append("")
    L.append("## 4. Divergências (todos os cenários), sem filtro\n")
    div = [l for l in linhas if (l["indice_classe"] in ("diferente", "sem prescrição") or l["reescrita_classe"] == "diferente"
                                 or (l["reescrita_classe"] == "sem prescrição" and l["antipadrao"] != "AP4" and l["cenario"] != "real_sem_colunas"))]
    if not div:
        L.append("- nenhuma")
    for l in div:
        L.append(f"- **{l['consulta']}** ({l['antipadrao']}), cenário `{l['cenario']}`: índice {l['indice_classe']}" + (f": {l['indice_motivo']}" if l["indice_motivo"] else "") +
                 f"; reescrita {l['reescrita_classe']}" + (f": {l['reescrita_motivo'][:300]}" if l["reescrita_motivo"] else ""))
    adicionais = [l for l in linhas if l["cenario"] == "real" and l["indices_adicionais"]]
    if adicionais:
        L.append("\nÍndices que a ferramenta propõe **além** do índice do catálogo (a classe acima só considera o índice da tabela do catálogo):\n")
        for l in adicionais:
            L.append(f"- {l['consulta']}: {l['indices_adicionais']}")
    L.append("")
    if est:
        L.append("## 5. Estimativas estáticas × medido na R1\n")
        L.append(f"Origem dos medidos: `{medidos['rodada']}` (cópia do SQLite). O modelo de tamanho foi ajustado a estes mesmos índices (sem coluna de tamanho variável o erro é ~0,5%); "
                 "para colunas de tamanho variável o tamanho médio é desconhecido (suposição: metade do declarado). As leituras estimadas usam o tamanho estimado do índice "
                 "(fração do predicado × páginas do índice) e a constante de 4 páginas por Key Lookup, **calibrada nas medições de C1 e do contraexemplo de S1**; "
                 "só consultas de uma tabela.\n")
        L.append("| tipo | consulta | índice | estimado | medido | erro relativo | origem |")
        L.append("|---|---|---|---|---|---|---|")
        for e in est:
            L.append(f"| {e['tipo']} | {e['consulta']} | {e['indice']} | {e['estimado']} | {e['medido']} | {e['erro_relativo']} | {e['origem']} |")
        L.append("")
    L.append("## 6. Verificação de equivalência de resultado (contra o banco)\n")
    if verif:
        L.append("| consulta | regra | status | linhas | detalhe |")
        L.append("|---|---|---|---|---|")
        for v in verif:
            L.append(f"| {v['consulta']} | {v['regra']} | {v['status']} | {v['linhas']} | {v['detalhe']} |")
        L.append("\n`equivalente_nos_dados` = mesmo *hash* de multiconjunto de linhas no banco consultado, uma execução; não prova equivalência lógica.")
    else:
        L.append("**NÃO EXECUTADA.** Todas as candidatas de reescrita estão como `nao_verificada`. Para executar (SOMENTE com o banco livre: o script recusa se achar `coletar.py` rodando):\n")
        L.append("```bash\ncd /Users/douglasbarra/code/TCC/pratica/nucleo\n.venv/bin/python ../experimento/avaliar_prescricao.py --verificar-no-banco [--banco StackOverflow2010]\n```\n")
        L.append("Ele roda `catalogo.verificar_equivalencia` (SELECTs de leitura) para cada candidata gerada em relação ao SQL original do catálogo e regrava este relatório com a coluna `verificacao_no_banco`.")
    L.append("")
    L.append("## 7. Reprodução\n")
    L.append("```bash\ncd /Users/douglasbarra/code/TCC/pratica/nucleo\n.venv/bin/python ../experimento/avaliar_prescricao.py --medidos <cópia de resultados/r1-compat160/tcclab.sqlite>\n```")
    return "\n".join(L) + "\n"


def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Avaliacao offline da Prescricao contra o catalogo")
    ap.add_argument("--catalogo", default=str(AQUI / "catalogo.toml"))
    ap.add_argument("--planos", default=str(AQUI / "planos"))
    ap.add_argument("--esquema", default=str(AQUI / "schema_so2010.json"))
    ap.add_argument("--saida", default=str(AQUI / "resultados" / "prescricao"))
    ap.add_argument("--medidos", help="SQLite de uma rodada (copie antes): compara tamanhos e leituras estimados com os medidos")
    ap.add_argument("--verificar-no-banco", action="store_true", help="CONECTA ao SQL Server e verifica a equivalencia de resultado das candidatas (banco livre!)")
    ap.add_argument("--banco", default="StackOverflow2010", help="banco usado na verificacao (somente leitura)")
    ap.add_argument("--mesmo-com-coleta-em-andamento", action="store_true", help="ignora a trava que recusa a verificacao durante uma coleta")
    return rodar(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(principal())
