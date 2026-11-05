"""Catalogo de consultas (TOML) -> variantes executaveis pelo runner.

Variantes geradas por consulta (letras na ordem ABA do runner):
  original, reescrita, indice, reescrita+indice, nativo (indice sugerido pelo otimizador), extras.
Antes de medir, `verificar_equivalencia` compara original e reescrita pelo hash de multiconjunto
das linhas (independe da ordem). Variante com resultado diferente e invalidada, nunca medida.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .driver import Conexao
from .runner import Variante

_RE_CREATE = re.compile(r"CREATE\s+(?:UNIQUE\s+)?(?:NONCLUSTERED\s+)?INDEX\s+(\w+)\s+ON\s+(\S+)", re.IGNORECASE)


@dataclass
class Extra:
    nome: str
    kind: str
    sql: str          # "original" | "reescrita"
    indice: str
    repeticoes_quente: int | None = None   # bracos muito lentos usam menos repeticoes
    repeticoes_frio: int | None = None
    aquecimentos: int | None = None


@dataclass
class Consulta:
    id: str
    antipadrao: str
    titulo: str
    tabelas: list[str]
    seletividade: str
    sql_original: str
    sql_reescrita: str | None = None
    sql_equivalencia: str | None = None
    indice: str | None = None
    indice_original: str | None = None
    papel: str = "principal"
    extras: list[Extra] = field(default_factory=list)


@dataclass
class Catalogo:
    versao: int
    banco: str
    consultas: list[Consulta]

    def por_id(self, qid: str) -> Consulta:
        for c in self.consultas:
            if c.id == qid:
                return c
        raise KeyError(qid)


def carregar(caminho: str | Path) -> Catalogo:
    dados = tomllib.loads(Path(caminho).read_text(encoding="utf-8"))
    meta = dados["meta"]
    consultas = []
    for c in dados["consulta"]:
        extras = [Extra(**e) for e in c.get("extra", [])]
        consultas.append(
            Consulta(
                id=c["id"], antipadrao=c["antipadrao"], titulo=c["titulo"], tabelas=list(c["tabelas"]),
                seletividade=c["seletividade"], sql_original=c["sql_original"].strip(),
                sql_reescrita=(c.get("sql_reescrita") or "").strip() or None,
                sql_equivalencia=(c.get("sql_equivalencia") or "").strip() or None,
                indice=c.get("indice"), indice_original=c.get("indice_original"),
                papel=c.get("papel", "principal"), extras=extras,
            )
        )
    ids = [c.id for c in consultas]
    if len(ids) != len(set(ids)):
        raise ValueError("ids duplicados no catalogo")
    return Catalogo(versao=int(meta["versao"]), banco=meta["banco"], consultas=consultas)


def ddl_reverter(create: str) -> str:
    m = _RE_CREATE.search(create)
    if not m:
        raise ValueError(f"nao reconheco o CREATE INDEX: {create!r}")
    return f"DROP INDEX {m.group(1)} ON {m.group(2)}"


def _assinatura_indice(create: str) -> tuple:
    """(tabela, chave ordenada, inclusoes como conjunto), em minusculas e sem colchetes; para comparar indices."""
    m = _RE_CREATE.search(create)
    tabela = m.group(2).lower().replace("[", "").replace("]", "")
    resto = create[m.end():]
    chave = re.search(r"\(([^)]*)\)", resto)
    incl = re.search(r"INCLUDE\s*\(([^)]*)\)", resto, re.IGNORECASE)
    limpar = lambda s: [x.strip().lower().replace("[", "").replace("]", "") for x in s.split(",") if x.strip()]  # noqa: E731
    return tabela, tuple(limpar(chave.group(1))) if chave else (), frozenset(limpar(incl.group(1))) if incl else frozenset()


def indice_nativo(indices_ausentes: list[dict]) -> str | None:
    """DDL do indice sugerido pelo otimizador (o de maior impacto); None se nao houve sugestao.
    Ordem das colunas-chave: igualdade, depois desigualdade (como o plano as lista)."""
    if not indices_ausentes:
        return None
    melhor = max(indices_ausentes, key=lambda i: i.get("impacto") or 0)
    cols = melhor["colunas"]
    chave = list(cols.get("EQUALITY", [])) + list(cols.get("INEQUALITY", []))
    if not chave:
        return None
    nome_tab = melhor["tabela"].replace("[", "").replace("]", "").split(".")[-1].lower()
    ddl = f"CREATE INDEX ix_exp_nativo_{nome_tab} ON {melhor['tabela']} ({', '.join(chave)})"
    incl = list(cols.get("INCLUDE", []))
    if incl:
        ddl += f" INCLUDE ({', '.join(incl)})"
    return ddl


def _var(nome: str, sql: str, kind: str, create: str | None) -> Variante:
    return Variante(
        nome=nome, sql=sql, kind=kind,
        ddl_aplicar=create, ddl_reverter=ddl_reverter(create) if create else None,
    )


def variantes_de(c: Consulta, nativo_ddl: str | None = None) -> tuple[list[Variante], list[str]]:
    """Variantes da consulta e notas (decisoes tomadas ao montar, por exemplo 'nativo igual ao framework')."""
    notas: list[str] = []
    vs = [_var("original", c.sql_original, "original", None)]
    if c.sql_reescrita:
        vs.append(_var("reescrita", c.sql_reescrita, "reescrita", None))
    if c.indice:
        vs.append(_var("indice", c.sql_original, "indice", c.indice_original or c.indice))
        if c.sql_reescrita:
            vs.append(_var("reescrita+indice", c.sql_reescrita, "prescricao", c.indice))
    if nativo_ddl:
        iguais = [x for x in (c.indice_original or c.indice, c.indice) if x and _assinatura_indice(x) == _assinatura_indice(nativo_ddl)]
        if iguais:
            notas.append("nativo igual ao indice do framework: braco nao medido separadamente")
        else:
            vs.append(_var("nativo", c.sql_original, "nativo", nativo_ddl))
    else:
        notas.append("otimizador nao sugeriu indice")
    for e in c.extras:
        v = _var(e.nome, c.sql_reescrita if e.sql == "reescrita" else c.sql_original, e.kind, e.indice)
        reps = {k: n for k, n in (("quente", e.repeticoes_quente), ("frio", e.repeticoes_frio)) if n is not None}
        v.repeticoes = reps or None
        v.aquecimentos = e.aquecimentos
        vs.append(v)
        if reps or e.aquecimentos is not None:
            notas.append(f"braco '{e.nome}' com repeticoes reduzidas {reps} e aquecimentos {e.aquecimentos} (execucao muito lenta)")
    return vs, notas


def verificar_equivalencia(con: Conexao, c: Consulta) -> dict:
    """Compara original (ou sql_equivalencia, quando a reescrita muda a projecao) com a reescrita."""
    if not c.sql_reescrita:
        return {"aplicavel": False, "ok": True}
    ref = c.sql_equivalencia or c.sql_original
    a = con.executar_lote(ref, hash_multiconjunto=True)
    b = con.executar_lote(c.sql_reescrita, hash_multiconjunto=True)
    ha = [x.hash_multiconjunto for x in a.conjuntos if not x.eh_plano_xml]
    hb = [x.hash_multiconjunto for x in b.conjuntos if not x.eh_plano_xml]
    linhas = sum(x.total_linhas for x in a.conjuntos if not x.eh_plano_xml)
    return {"aplicavel": True, "ok": ha == hb and bool(ha), "linhas": linhas, "hash_referencia": ha, "hash_reescrita": hb}
