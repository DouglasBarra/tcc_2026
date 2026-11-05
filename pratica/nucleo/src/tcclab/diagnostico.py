"""Diagnostico (camada 1 do framework): antipadroes de escrita numa consulta T-SQL, com ou sem plano de execucao.

Entrada: o texto da consulta e, opcionalmente, o plano XML (real ou estimado). Saida: `Diagnostico`, com a lista de
`Achado` (regra, antipadrao AP1..AP4, gravidade, evidencia objetiva, explicacao) e o registro explicito do que NAO foi
executado (sem plano, SQL nao analisavel).

Regras (identificadores estaveis; limiares em `Limiares`; detalhes e estado de teste em knowledge/24-diagnostico.md):
  SQL (sqlglot, dialeto tsql)             plano (XML, biblioteca padrao)
  S1 SELECT *                    -> AP3   P1 varredura com predicado residual sobre tabela grande -> AP4
  S2 funcao/conversao em coluna  -> AP1   P2 Key Lookup / RID Lookup com muitas execucoes        -> AP3
  S3 subconsulta escalar                  P3 funcao/conversao sobre coluna no predicado da varredura -> AP1
     correlacionada              -> AP2   P4 aviso do plano (PlanAffectingConvert -> AP1; outros sem AP)
                                          P5 indice ausente sugerido pelo otimizador            -> AP4
                                          P6 Nested Loops correlacionado, lado interno executado muitas vezes -> AP2
Evidencia cruzada: o achado e "confirmado" quando SQL e plano concordam (S1-P2, S2-P3/P4, S3-P6; P1/P5 quando a coluna
do indice ou do filtro tambem esta nos predicados do SQL); so SQL ou so plano e "indicio". Sem plano, so S1 a S3 rodam
e isso consta em `Diagnostico.regras_ignoradas`. ParseError (ou texto que nao e uma instrucao reconhecida) nunca vira
"sem problemas": o SQL fica marcado como nao analisavel.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from .plano import NS, Instrucao, Operador, analisar_plano

try:  # extra opcional `diagnostico`: sem ele so as regras de plano rodam
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.scope import build_scope
    from sqlglot.tokens import TokenType

    _COMPARACOES = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NullSafeEQ, exp.NullSafeNEQ)
    _ARITMETICA = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.IntDiv, exp.Mod)
except ImportError:  # pragma: no cover - ambiente sem o extra
    sqlglot = None

Gravidade = Literal["alta", "media", "baixa"]
Nivel = Literal["confirmado", "indicio"]
_ORDEM_GRAVIDADE = {"alta": 0, "media": 1, "baixa": 2}

_VARREDURAS = ("Table Scan", "Clustered Index Scan", "Index Scan")
# funcoes internas do plano que nao tornam o predicado nao sargavel (LIKE vira intervalo; marcadores do otimizador)
# (PROBE = filtro de bitmap gerado pelo otimizador em juncoes hash; nao e predicado escrito pelo autor)
_INTRINSECAS_IGNORADAS = ("like", "likerangestart", "likerangeend", "likerangeinfo", "isfalseornull", "probe")
_ESTRUTURA_SQL = frozenset(
    "AND OR NOT WHERE ON HAVING IN EXISTS SELECT WHEN THEN ELSE CASE BETWEEN LIKE IS JOIN FROM BY SET AS WITH UNION "
    "ALL ANY SOME DISTINCT TOP OPTION CROSS INNER OUTER FULL APPLY".split()   # LEFT e RIGHT tambem sao funcoes: ficam de fora
)
_FRONTEIRAS_SQL = frozenset("AND OR WHERE GROUP ORDER HAVING JOIN ON UNION OPTION FROM THEN ELSE END INNER LEFT RIGHT CROSS FULL".split())
_FRONTEIRAS_ESQ = frozenset("AND OR WHERE ON HAVING WHEN THEN ELSE SELECT JOIN BY".split())


# ----------------------------------------------------------------------------------------------- regras e limiares
@dataclass(frozen=True)
class Regra:
    id: str
    antipadrao: str | None   # classe do catalogo (AP1..AP4) a que a regra corresponde
    origem: Literal["sql", "plano"]
    titulo: str


REGRAS: dict[str, Regra] = {
    r.id: r
    for r in (
        Regra("S1", "AP3", "sql", "SELECT * (colunas nao listadas)"),
        Regra("S2", "AP1", "sql", "funcao ou conversao aplicada a coluna em predicado (nao sargavel)"),
        Regra("S3", "AP2", "sql", "subconsulta escalar correlacionada"),
        Regra("P1", "AP4", "plano", "varredura com predicado residual sobre tabela grande"),
        Regra("P2", "AP3", "plano", "Key Lookup / RID Lookup com muitas execucoes"),
        Regra("P3", "AP1", "plano", "funcao ou conversao sobre coluna no predicado residual de varredura"),
        Regra("P4", "AP1", "plano", "aviso do plano (PlanAffectingConvert -> AP1; os demais sem antipadrao)"),
        Regra("P5", "AP4", "plano", "indice ausente sugerido pelo otimizador"),
        Regra("P6", "AP2", "plano", "Nested Loops correlacionado com muitas execucoes do lado interno"),
    )
}
ANTIPADRAO_DA_REGRA: dict[str, str | None] = {r.id: r.antipadrao for r in REGRAS.values()}


@dataclass(frozen=True)
class Limiares:
    """Limiares das regras. Todos sao SUPOSICOES a calibrar (nenhum veio de teoria); mudar so com registro no pre-registro."""
    # P1: varredura com predicado residual
    p1_linhas_minimas: int = 100_000     # tabela "grande": TableCardinality >= este valor (abaixo, a varredura nao e acusada)
    p1_fracao_maxima: float = 0.20       # so acusa se o predicado deixa passar <= 20% das linhas lidas (limite media/baixa do catalogo)
    p1_fracao_alta: float = 0.01         # <= 1% das linhas: gravidade alta (classe "alta seletividade" do catalogo)
    p1_leitura_minima: float = 0.50      # plano real: a varredura precisa ter lido >= 50% da tabela (varredura interrompida nao conta)
    # P2: Key Lookup / RID Lookup
    p2_execucoes_minimas: int = 1_000    # execucoes do lookup (real) ou linhas estimadas do lado externo (estimado)
    p2_execucoes_altas: int = 10_000     # a partir daqui, gravidade alta
    # P5: indice ausente
    p5_impacto_minimo: float = 50.0      # "Impact" do MissingIndexGroup (% de reducao estimada de custo) minimo para reportar
    p5_impacto_alto: float = 80.0        # a partir daqui, gravidade alta
    # P6: Nested Loops correlacionado
    p6_execucoes_minimas: int = 1_000    # execucoes do lado interno
    p6_execucoes_altas: int = 10_000
    # S3
    s3_subconsultas_alta: int = 2        # >= 2 subconsultas escalares correlacionadas sobre a mesma tabela: gravidade alta


@dataclass
class Achado:
    regra: str
    antipadrao: str | None
    gravidade: Gravidade
    nivel: Nivel
    fontes: tuple[str, ...]              # ("sql",), ("plano",) ou ("sql", "plano")
    evidencia: str                       # fatos objetivos: no do plano, operador, contadores, trecho do SQL
    explicacao: str                      # leitura curta em pt-BR
    trecho_sql: str | None = None
    linha_sql: int | None = None
    no_plano: int | None = None
    operador: str | None = None
    tabelas: tuple[str, ...] = ()        # nomes simples, como no SQL/plano (sem colchetes)
    colunas: tuple[str, ...] = ()
    id: str = ""                         # ex.: "S2-1" (unico dentro do diagnostico)
    corrobora: tuple[str, ...] = ()      # ids dos achados do outro lado que concordam
    notas: tuple[str, ...] = ()
    detalhes: dict = field(default_factory=dict)

    @property
    def confirmado(self) -> bool:
        return self.nivel == "confirmado"


@dataclass
class Diagnostico:
    achados: list[Achado]
    sql_analisavel: bool
    motivo_sql: str | None               # por que o SQL nao foi analisado (None se foi)
    plano: Literal["nenhum", "estimado", "real", "invalido"]
    regras_executadas: list[str]
    regras_ignoradas: dict[str, str]     # regra -> motivo (ex.: "sem plano de execucao")
    avisos: list[str]
    limiares: Limiares

    def da_regra(self, regra: str) -> list[Achado]:
        return [a for a in self.achados if a.regra == regra]

    def antipadroes(self, nivel: Nivel | None = None) -> set[str]:
        """Antipadroes (AP1..AP4) com algum achado; `nivel` restringe a confirmados ou a indicios."""
        return {a.antipadrao for a in self.achados if a.antipadrao and (nivel is None or a.nivel == nivel)}

    def nivel_do_antipadrao(self, ap: str) -> Nivel | None:
        """Melhor nivel de evidencia do antipadrao (confirmado vence indicio); None se nao detectado."""
        niveis = {a.nivel for a in self.achados if a.antipadrao == ap}
        return "confirmado" if "confirmado" in niveis else ("indicio" if niveis else None)

    def para_dict(self) -> dict:
        return asdict(self)

    def para_json(self) -> str:
        return json.dumps(self.para_dict(), ensure_ascii=False, indent=2, default=str)


# ----------------------------------------------------------------------------------------------- utilitarios
def _n(x: float | int | None) -> str:
    """Numero no formato pt-BR (milhar com ponto, decimal com virgula)."""
    if x is None:
        return "?"
    if float(x).is_integer():
        return f"{int(x):,}".replace(",", ".")
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(f: float) -> str:
    p = f * 100
    return (f"{p:.2f}" if p < 10 else f"{p:.1f}").replace(".", ",") + "%"


def _sem_colchetes(s: str | None) -> str | None:
    return s.replace("[", "").replace("]", "") if s else s


def _curto(s: str, limite: int = 160) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limite else s[: limite - 3] + "..."


def _tag(e: ET.Element) -> str:
    return e.tag.removeprefix(NS)


def _gravidade_por_contagem(n: float, alto: float) -> Gravidade:
    return "alta" if n >= alto else "media"


# ----------------------------------------------------------------------------------------------- texto do SQL
class _Fonte:
    """Texto original + tokens do sqlglot: recupera trechos e linhas do SQL como o autor escreveu (nao o SQL regerado)."""

    def __init__(self, sql: str):
        self.sql = sql
        self.tokens = sqlglot.tokenize(sql, read="tsql")
        self._por_inicio = {t.start: i for i, t in enumerate(self.tokens)}

    def _intervalo(self, no) -> tuple[int, int] | None:
        """Indices do primeiro e do ultimo token das folhas (identificadores e literais) com posicao conhecida."""
        idx = [
            self._por_inicio[n.meta["start"]]
            for n in _descendentes(no, lambda _: False)
            if isinstance(n, (exp.Identifier, exp.Literal)) and n.meta.get("start") in self._por_inicio
        ]
        return (min(idx), max(idx)) if idx else None

    def _saldo(self, i: int, j: int) -> tuple[int, int]:
        """(')' sem '(' , '(' sem ')') no intervalo de tokens."""
        saldo = minimo = 0
        for t in self.tokens[i : j + 1]:
            if t.token_type == TokenType.L_PAREN:
                saldo += 1
            elif t.token_type == TokenType.R_PAREN:
                saldo -= 1
                minimo = min(minimo, saldo)
        return -minimo, saldo - minimo

    def _nome_de_funcao(self, k: int) -> bool:
        t = self.tokens[k]
        return t.token_type != TokenType.STRING and bool(re.fullmatch(r"[A-Za-z_]\w*", t.text)) and t.text.upper() not in _ESTRUTURA_SQL

    def _fecha(self, k: int) -> int | None:
        """Indice do ')' que fecha o '(' do token k."""
        prof = 0
        for m in range(k, len(self.tokens)):
            tp = self.tokens[m].token_type
            prof += tp == TokenType.L_PAREN
            prof -= tp == TokenType.R_PAREN
            if prof == 0:
                return m
        return None

    def _ajustar(self, i: int, j: int) -> tuple[int, int]:
        """Equilibra parenteses e inclui a chamada de funcao que envolve o intervalo (nome, '(' e ')')."""
        n = len(self.tokens)
        while True:
            esq, dir_ = self._saldo(i, j)
            if esq > 0 and i > 0:
                i -= 1
            elif dir_ > 0 and j + 1 < n:
                j += 1
            elif i >= 1 and self.tokens[i].token_type == TokenType.L_PAREN and self._nome_de_funcao(i - 1):
                i -= 1                                    # nome da funcao antes do '('
                while i >= 2 and self.tokens[i - 1].token_type == TokenType.DOT:
                    i -= 2                                # dbo.funcao(
            elif i >= 2 and self.tokens[i - 1].token_type == TokenType.L_PAREN and self._nome_de_funcao(i - 2) and (k := self._fecha(i - 1)) is not None:
                i, j = i - 2, max(j, k)                   # intervalo logo depois do '(' de uma chamada
            else:
                return i, j

    def trecho(self, no, limite: int = 150) -> tuple[str, int] | None:
        """(texto original do no, linha). None se nao ha posicao (no criado por normalizacao do sqlglot)."""
        iv = self._intervalo(no)
        if iv is None:
            return None
        n = len(self.tokens)
        i, j = self._ajustar(*iv)
        prof = 0
        while j + 1 < n:  # operando da direita que nao e folha com posicao (N'x', @p, GETDATE())
            t = self.tokens[j + 1]
            if t.token_type == TokenType.R_PAREN:
                if prof == 0:
                    break
                prof -= 1
            elif t.token_type == TokenType.L_PAREN:
                prof += 1
            elif prof == 0 and t.token_type != TokenType.STRING and (t.text.upper().split() or [""])[0] in _FRONTEIRAS_SQL | {";"}:
                break  # o token "GROUP BY" traz as duas palavras
            j += 1
        prof = 0
        while i > 0:      # operando da esquerda que nao e folha com posicao
            t = self.tokens[i - 1]
            if t.token_type == TokenType.L_PAREN:
                if prof == 0:
                    break
                prof -= 1
            elif t.token_type == TokenType.R_PAREN:
                prof += 1
            elif prof == 0 and (t.token_type == TokenType.COMMA or (t.token_type != TokenType.STRING and (t.text.upper().split() or [""])[0] in _FRONTEIRAS_ESQ)):
                break
            i -= 1
        i, j = self._ajustar(i, j)
        texto = self.sql[self.tokens[i].start : self.tokens[j].end + 1]
        return _curto(texto, limite), self.tokens[i].line

    def trecho_subconsulta(self, sub, limite: int = 150) -> tuple[str, int] | None:
        """Texto da subconsulta com os parenteses: sobe dos tokens internos ate o '(' seguido de SELECT."""
        iv = self._intervalo(sub)
        if iv is None:
            return None
        k, prof = iv[0], 0
        while k > 0:
            k -= 1
            t = self.tokens[k]
            if t.token_type == TokenType.R_PAREN:
                prof += 1
            elif t.token_type == TokenType.L_PAREN:
                if prof > 0:
                    prof -= 1
                elif self.tokens[k + 1].token_type == TokenType.SELECT:
                    break
        else:
            return None
        j = self._fecha(k)
        if j is None:
            return None
        return _curto(self.sql[self.tokens[k].start : self.tokens[j].end + 1], limite), self.tokens[k].line

    def linha_do_select_estrela(self) -> int | None:
        m = re.search(r"\bSELECT\s+(?:ALL\s+|DISTINCT\s+)?(?:TOP\s*(?:\(\s*\d+\s*\)|\d+)(?:\s+PERCENT)?\s+)?(?:[\w\[\]]+\.)?\*", self.sql, re.I)
        return self.sql.count("\n", 0, m.start()) + 1 if m else None


def _descendentes(no, podar) -> Iterator:
    """No e descendentes; `podar(filho)` verdadeiro nao desce nesse filho (nem o devolve)."""
    pilha = [no]
    while pilha:
        n = pilha.pop()
        yield n
        pilha.extend(f for f in n.iter_expressions() if not podar(f))


def _sem_select(f) -> bool:
    return isinstance(f, exp.Select)


# ----------------------------------------------------------------------------------------------- regras de SQL
@dataclass
class _FatosSql:
    tabelas: set[str] = field(default_factory=set)                  # nomes simples (minusculas) das tabelas base
    filtros: set[tuple[str | None, str]] = field(default_factory=set)  # (tabela|None, coluna) em predicados WHERE/ON/HAVING


def _tabelas_do_select(sel, ctes: set[str]) -> dict[str, str | None]:
    """alias (minusculo) -> nome simples da tabela base como escrito no SQL; None para CTE ou tabela derivada. So os do proprio SELECT."""
    fontes = []
    de = sel.args.get("from_") or sel.args.get("from")
    if de is not None:
        fontes.append(de.this)
    if isinstance(sel, (exp.Update, exp.Delete)):
        fontes.append(sel.this)
    fontes += [j.this for j in sel.args.get("joins") or []]
    mapa: dict[str, str | None] = {}
    for f in fontes:
        if isinstance(f, exp.Table):
            mapa[(f.alias or f.name).lower()] = None if f.name.lower() in ctes else f.name
        elif f is not None and f.alias:
            mapa[f.alias.lower()] = None
    return mapa


def _nome_da_tabela(f) -> str:
    return ".".join(p for p in (f.db, f.name) if p)


def _tabela_da_coluna(col, mapa: dict[str, str | None]) -> str | None:
    if col.table:
        return mapa.get(col.table.lower())
    return next(iter(mapa.values())) if len(mapa) == 1 else None


def _escopo_de(no):
    return no.find_ancestor(exp.Select, exp.Update, exp.Delete)


def _operandos(cmp_) -> list:
    """Lados de uma comparacao que podem conter coluna dentro de funcao."""
    if isinstance(cmp_, _COMPARACOES):
        return [cmp_.this, cmp_.expression]
    if isinstance(cmp_, (exp.Like, exp.ILike, exp.Is, exp.In)):
        return [cmp_.this]
    if isinstance(cmp_, exp.Between):
        return [cmp_.this, cmp_.args.get("low"), cmp_.args.get("high")]
    return []


def _aplica_funcao(op) -> bool:
    """O operando e uma funcao, conversao ou aritmetica (e nao coluna, literal, parametro, subconsulta ou agregacao)?"""
    while isinstance(op, exp.Paren):
        op = op.this
    if isinstance(op, exp.Dot):
        return isinstance(op.expression, exp.Func)
    if isinstance(op, (exp.AggFunc, exp.Collate)):
        return False
    return isinstance(op, (exp.Func, *_ARITMETICA)) and not isinstance(op, (exp.Subquery, exp.Select))


def _colunas_do_operando(op) -> list:
    cols = [n for n in _descendentes(op, _sem_select) if isinstance(n, exp.Column) and not isinstance(n.this, exp.Star)]
    return sorted(cols, key=lambda c: c.meta.get("start", 0))


def _nomes_de_funcao(trecho: str | None, op) -> list[str]:
    """Nomes das funcoes como escritos no SQL (YEAR, UPPER, CAST...); aritmetica quando nao ha chamada."""
    nomes = re.findall(r"([A-Za-z_]\w*)\s*\(", trecho or "")
    nomes = [x for x in nomes if x.upper() not in _ESTRUTURA_SQL]
    return nomes or (["aritmetica"] if isinstance(op, _ARITMETICA) else [type(op).__name__])


def _regra_s1(arvore, fonte: _Fonte, ctes: set[str]) -> list[Achado]:
    achados = []
    for sel in arvore.find_all(exp.Select):
        estrelas = [e for e in sel.expressions if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))]
        if not estrelas or isinstance(sel.parent, exp.Exists):  # EXISTS (SELECT * ...) e idiomatico e inofensivo
            continue
        qualif = {e.table.lower() for e in estrelas if isinstance(e, exp.Column) and e.table}   # t.* limita as tabelas
        bases = sorted({
            _nome_da_tabela(t) for t in _fontes_tabela(sel)
            if t.name.lower() not in ctes and (not qualif or (t.alias or t.name).lower() in qualif)
        })
        direta = bool(bases)
        alvo = ", ".join(bases) if bases else "subconsulta/CTE"
        linha = fonte.linha_do_select_estrela()
        achados.append(Achado(
            regra="S1", antipadrao="AP3", gravidade="alta" if direta else "baixa", nivel="indicio", fontes=("sql",),
            evidencia=f"SELECT * sobre {alvo}" + (f" (linha {linha})" if linha else "") + ": nenhuma coluna listada.",
            explicacao=(
                "SELECT * devolve todas as colunas da tabela, inclusive as que a aplicação não usa. Isso aumenta o volume lido e "
                "transferido e impede que um índice com poucas colunas atenda a consulta sozinho (obriga buscas extras na tabela "
                "principal). Liste só as colunas necessárias."
                if direta else
                "SELECT * sobre uma subconsulta ou CTE: repassa todas as colunas dela adiante. Menos grave que sobre a tabela, "
                "mas ainda esconde quais colunas a consulta realmente usa."
            ),
            trecho_sql=_curto(f"SELECT {', '.join(e.sql(dialect='tsql') for e in estrelas)} FROM {alvo}", 100), linha_sql=linha,
            tabelas=tuple(b.split(".")[-1] for b in bases), detalhes={"estrelas": [e.sql(dialect="tsql") for e in estrelas]},
        ))
    return achados


def _fontes_tabela(sel) -> list:
    de = sel.args.get("from_") or sel.args.get("from")
    fontes = ([de.this] if de is not None else []) + [j.this for j in sel.args.get("joins") or []]
    return [f for f in fontes if isinstance(f, exp.Table)]


def _predicados(arvore) -> Iterator[tuple[str, object]]:
    """(clausula, no raiz do predicado) de WHERE, ON e HAVING de toda a arvore."""
    for w in arvore.find_all(exp.Where):
        yield "WHERE", w.this
    for h in arvore.find_all(exp.Having):
        yield "HAVING", h.this
    for j in arvore.find_all(exp.Join):
        if j.args.get("on") is not None:
            yield "JOIN ... ON", j.args["on"]


def _regra_s2(arvore, fonte: _Fonte, ctes: set[str], fatos: _FatosSql) -> list[Achado]:
    achados = []
    for clausula, raiz in _predicados(arvore):
        escopo = _escopo_de(raiz)
        mapa = _tabelas_do_select(escopo, ctes) if escopo is not None else {}
        for cmp_ in (n for n in _descendentes(raiz, _sem_select) if _operandos(n)):
            for op in _operandos(cmp_):
                if op is None:
                    continue
                for c in _colunas_do_operando(op):  # filtros: toda coluna comparada, com ou sem funcao
                    t = _tabela_da_coluna(c, mapa)
                    fatos.filtros.add((t.lower() if t else None, c.name.lower()))
                if not _aplica_funcao(op) or not (cols := _colunas_do_operando(op)):
                    continue
                trecho = fonte.trecho(cmp_)
                texto = trecho[0] if trecho else _curto(cmp_.sql(dialect="tsql"))
                linha = trecho[1] if trecho else None
                funcs = _nomes_de_funcao(trecho[0] if trecho else None, op)
                tabs = tuple(dict.fromkeys(t for t in (_tabela_da_coluna(c, mapa) for c in cols) if t))
                nomes_col = tuple(dict.fromkeys(c.name for c in cols))
                alvo = ", ".join(f"{t}.{n}" for t, n in zip(tabs, nomes_col)) if len(tabs) == len(nomes_col) else ", ".join(nomes_col)
                achados.append(Achado(
                    regra="S2", antipadrao="AP1", gravidade="alta", nivel="indicio", fontes=("sql",),
                    evidencia=f"{'/'.join(dict.fromkeys(funcs))} sobre a coluna {alvo} em {clausula}"
                              + (f" (linha {linha})" if linha else "") + f": {texto}",
                    explicacao=(
                        f"A expressão {'/'.join(dict.fromkeys(funcs))} aplicada à coluna {alvo} faz o servidor calcular o valor para cada "
                        "linha antes de comparar, então um índice sobre essa coluna não serve para localizar as linhas (predicado não "
                        "sargável). Reescreva comparando a coluna diretamente com um valor ou intervalo equivalente."
                    ),
                    trecho_sql=texto, linha_sql=linha, tabelas=tabs, colunas=nomes_col,
                    detalhes={"funcoes": funcs, "clausula": clausula},
                ))
    return achados


def _regra_s3(arvore, fonte: _Fonte, lim: Limiares) -> list[Achado]:
    raiz = build_scope(arvore)
    if raiz is None:
        return []
    brutos = []
    for escopo in raiz.traverse():
        if not escopo.is_subquery:
            continue
        pai = escopo.expression.parent
        if isinstance(pai, exp.Exists) or (isinstance(pai, exp.Subquery) and isinstance(pai.parent, (exp.In, exp.Any, exp.All, exp.Lateral))):
            continue  # EXISTS / IN / ANY / ALL: semijuncao; CROSS/OUTER APPLY: escolha deliberada; fora do escopo do AP2 (escalar)
        externas = [c for c in escopo.external_columns if c.table]   # coluna sem qualificador nao permite afirmar correlacao
        if not externas:
            continue
        internas = sorted({t.name for t in escopo.sources.values() if isinstance(t, exp.Table)})
        sub = pai if isinstance(pai, exp.Subquery) else escopo.expression
        trecho = fonte.trecho_subconsulta(escopo.expression)
        brutos.append((internas, sorted({c.sql(dialect="tsql") for c in externas}), sub, trecho))
    por_tabela: dict[str, int] = {}
    for internas, *_ in brutos:
        for t in internas:
            por_tabela[t.lower()] = por_tabela.get(t.lower(), 0) + 1
    achados = []
    for internas, externas, sub, trecho in brutos:
        n_mesma = max((por_tabela[t.lower()] for t in internas), default=1)
        texto = trecho[0] if trecho else _curto(sub.sql(dialect="tsql"))
        linha = trecho[1] if trecho else None
        alvo = ", ".join(internas) or "?"
        achados.append(Achado(
            regra="S3", antipadrao="AP2", gravidade="alta" if n_mesma >= lim.s3_subconsultas_alta else "media", nivel="indicio",
            fontes=("sql",),
            evidencia=f"Subconsulta escalar sobre {alvo} correlacionada com {', '.join(externas)}" + (f" (linha {linha})" if linha else "")
                      + f": {texto}" + (f" [{n_mesma} subconsultas correlacionadas sobre {alvo}]" if n_mesma > 1 else ""),
            explicacao=(
                f"A subconsulta usa {', '.join(externas)} da consulta externa; logicamente ela é avaliada uma vez por linha externa. "
                "Quando há várias subconsultas sobre a mesma tabela, uma junção com agregação costuma ler a tabela menos vezes. "
                "O otimizador às vezes faz essa transformação sozinho; o plano mostra se fez."
            ),
            trecho_sql=texto, linha_sql=linha, tabelas=tuple(t.lower() for t in internas),
            detalhes={"externas": externas, "subconsultas_na_mesma_tabela": n_mesma},
        ))
    return achados


LIMIARES_PADRAO = Limiares()


def _analisar_sql(sql: str, lim: Limiares) -> tuple[list[Achado], _FatosSql, str | None, list[str]]:
    """(achados, fatos, motivo de nao analisavel, avisos). Nunca lanca por causa do texto."""
    fatos, avisos = _FatosSql(), []
    if sqlglot is None:
        return [], fatos, "sqlglot nao instalado (extra `diagnostico`)", avisos
    try:
        arvores = [a for a in sqlglot.parse(sql, read="tsql") if a is not None]
        fonte = _Fonte(sql)
    except (SqlglotError, RecursionError) as e:
        return [], fatos, f"ParseError: {_curto(str(e), 200)}", avisos
    uteis = [a for a in arvores if a.find(exp.Select, exp.Update, exp.Delete, exp.Insert, exp.Merge)]
    if not uteis:
        # o parser e leniente: texto invalido pode virar expressao qualquer; so aceitamos instrucao com SELECT/UPDATE/DELETE/INSERT/MERGE
        tipo = type(arvores[0]).__name__ if arvores else "vazio"
        return [], fatos, f"texto nao reconhecido como instrucao SELECT/UPDATE/DELETE/INSERT ({tipo})", avisos
    if len(uteis) < len(arvores):
        avisos.append(f"{len(arvores) - len(uteis)} instrucao(oes) sem consulta (DECLARE, SET, EXEC...) ignorada(s) pelas regras de SQL")
    achados: list[Achado] = []
    for arvore in uteis:
        ctes = {c.alias.lower() for c in arvore.find_all(exp.CTE)}
        for t in arvore.find_all(exp.Table):
            if t.name.lower() not in ctes:
                fatos.tabelas.add(t.name.lower())
        achados += _regra_s1(arvore, fonte, ctes)
        achados += _regra_s2(arvore, fonte, ctes, fatos)
        achados += _regra_s3(arvore, fonte, lim)
    return achados, fatos, None, avisos


# ----------------------------------------------------------------------------------------------- arvore do plano
@dataclass
class _No:
    elem: ET.Element
    op: Operador
    pai: "_No | None" = None
    filhos: list["_No"] = field(default_factory=list)
    tabela: str | None = None            # esquema.tabela, sem colchetes
    indice: str | None = None
    predicado: ET.Element | None = None  # <Predicate> proprio do operador (residual)
    avisos: list[ET.Element] = field(default_factory=list)
    correlacionado: bool = False         # Nested Loops com OuterReferences (lado interno recebe valores do externo)

    @property
    def nome(self) -> str:
        """Nome de exibicao: o Key Lookup aparece como 'Clustered Index Seek' com Lookup=1 nas versoes recentes."""
        if self.op.lookup and self.op.physical_op != "RID Lookup":
            return "Key Lookup"
        return self.op.physical_op

    @property
    def simples(self) -> str | None:
        return self.tabela.split(".")[-1] if self.tabela else None


def _filhos_relop(elem: ET.Element) -> Iterator[ET.Element]:
    for f in elem:
        if f.tag == NS + "RelOp":
            yield f
        else:
            yield from _filhos_relop(f)


def _construir(elem: ET.Element, ops: dict[int, Operador], pai: _No | None = None) -> _No:
    op = ops.get(int(elem.get("NodeId", "-1"))) or Operador(0, elem.get("PhysicalOp", ""), elem.get("LogicalOp", ""))
    no = _No(elem=elem, op=op, pai=pai)
    for f in elem:
        if f.tag == NS + "Warnings":
            no.avisos.append(f)
        elif f.tag not in (NS + "RunTimeInformation", NS + "OutputList", NS + "MemoryFractions"):
            for sub in f:
                if sub.tag == NS + "Object" and no.tabela is None:
                    no.tabela = _sem_colchetes(".".join(p for p in (sub.get("Schema"), sub.get("Table")) if p)) or None
                    no.indice = _sem_colchetes(sub.get("Index"))
                elif sub.tag == NS + "Predicate" and no.predicado is None:
                    no.predicado = sub
                elif sub.tag == NS + "OuterReferences" and _tag(f) == "NestedLoops":
                    no.correlacionado = True
    no.filhos = [_construir(r, ops, no) for r in _filhos_relop(elem)]
    return no


def _percorrer(no: _No) -> Iterator[_No]:
    yield no
    for f in no.filhos:
        yield from _percorrer(f)


def _execucoes(no: _No, real: bool) -> float | None:
    """Execucoes do operador: contadores reais; no estimado, linhas estimadas do lado externo do Nested Loops pai."""
    if real:
        # Compute Scalar e similares aparecem com 0 execucoes no plano real: vale o 1o operador abaixo que registrou execucoes
        return float(next((s.op.actual_executions for s in _percorrer(no) if s.op.actual_executions > 0), 0))
    if no.pai is not None and no.pai.op.physical_op == "Nested Loops" and no.pai.filhos and no is not no.pai.filhos[0]:
        return no.pai.filhos[0].op.est_rows
    return 1.0 if no.pai is None or no.pai.op.physical_op != "Nested Loops" else None


@dataclass
class _UsoFuncao:
    tipo: str          # funcao | conversao | conversao_implicita | aritmetica | funcao_usuario
    nome: str
    tabela: str        # nome simples
    coluna: str
    texto: str         # ScalarString do predicado


def _funcoes_sobre_colunas(pred: ET.Element) -> list[_UsoFuncao]:
    """Funcoes/conversoes cujo argumento e coluna de tabela. CONVERT_IMPLICIT sobre PARAMETRO ou constante (o caso comum
    da parametrizacao simples, `col = CONVERT_IMPLICIT(int,[@1],0)`) nao conta: so conta quando a coluna e quem converte."""
    so = pred.find(NS + "ScalarOperator")
    texto = so.get("ScalarString", "") if so is not None else ""
    usos: list[_UsoFuncao] = []
    for e in pred.iter():
        t = _tag(e)
        if t == "Intrinsic":
            nome = e.get("FunctionName", "")
            if nome.lower() in _INTRINSECAS_IGNORADAS:
                continue
            tipo = "funcao"
        elif t == "Convert":
            implicita = e.get("Implicit") in ("1", "true")
            tipo, nome = ("conversao_implicita", "CONVERT_IMPLICIT") if implicita else ("conversao", "CONVERT/CAST")
            nome += f"({e.get('DataType', '?')})"
        elif t == "Arithmetic":
            tipo, nome = "aritmetica", e.get("Operation", "ARITMETICA")
        elif t == "UserDefinedFunction":
            tipo, nome = "funcao_usuario", e.get("FunctionName", "UDF")
        else:
            continue
        for cr in e.iter(NS + "ColumnReference"):
            if cr.get("Table") and cr.get("Column"):
                usos.append(_UsoFuncao(tipo, nome, _sem_colchetes(cr.get("Table")), _sem_colchetes(cr.get("Column")), texto))
    return _unicos(usos)


def _unicos(usos: list[_UsoFuncao]) -> list[_UsoFuncao]:
    vistos, saida = set(), []
    for u in usos:
        k = (u.tipo, u.nome, u.tabela.lower(), u.coluna.lower())
        if k not in vistos:
            vistos.add(k)
            saida.append(u)
    return saida


def _predicado_do_autor(pred: ET.Element) -> bool:
    """O predicado tem algo alem de PROBE (filtro de bitmap que o otimizador cria em juncoes hash)?"""
    return any(_tag(e) == "Compare" or (_tag(e) == "Intrinsic" and e.get("FunctionName", "").lower() != "probe") for e in pred.iter())


def _colunas_da_expressao(expr: str) -> list[tuple[str, str]]:
    """(tabela, coluna) das referencias qualificadas `[db].[esq].[tabela].[coluna]` num texto de expressao do plano."""
    refs = []
    for cadeia in re.findall(r"(?:\[[^\]]+\]\.)+\[[^\]]+\]", expr):
        partes = re.findall(r"\[([^\]]+)\]", cadeia)
        if len(partes) >= 2:
            refs.append((partes[-2], partes[-1]))
    return refs


def _colunas_do_predicado(pred: ET.Element) -> set[str]:
    return {_sem_colchetes(cr.get("Column")).lower() for cr in pred.iter(NS + "ColumnReference") if cr.get("Table") and cr.get("Column")}


# ----------------------------------------------------------------------------------------------- regras de plano
def _regra_p1(nos: list[_No], real: bool, lim: Limiares) -> list[Achado]:
    achados = []
    for no in nos:
        op = no.op
        if op.physical_op not in _VARREDURAS or no.predicado is None or not _predicado_do_autor(no.predicado):
            continue  # varredura SEM filtro (ou so com filtro de bitmap de juncao) e legitima: nao e acusada
        card = op.table_cardinality
        if not card or card < lim.p1_linhas_minimas or (real and op.actual_executions == 0):
            continue
        lidas = op.actual_rows_read if real and op.actual_rows_read else (op.est_rows_read or card)
        saida = op.actual_rows if real else op.est_rows
        if not lidas or saida is None or (real and lidas < lim.p1_leitura_minima * card):
            continue
        fracao = saida / lidas
        if fracao > lim.p1_fracao_maxima:
            continue
        base = "leu" if real else "estima ler"
        achados.append(Achado(
            regra="P1", antipadrao="AP4", gravidade="alta" if fracao <= lim.p1_fracao_alta else "media", nivel="indicio", fontes=("plano",),
            evidencia=f"Nó {op.node_id} ({op.physical_op}) em {no.tabela} [{'plano real' if real else 'plano estimado'}]: {base} {_n(lidas)} de "
                      f"{_n(card)} linhas e {'devolveu' if real else 'estima devolver'} {_n(saida)} ({_pct(fracao)}); predicado residual: {_curto(op.predicado, 120)}",
            explicacao=(
                "O plano lê a tabela inteira e descarta quase tudo no filtro: não há índice que permita ir direto às linhas desejadas. "
                "Considere um índice nas colunas do filtro (a camada de análise avalia o custo de escrita)."
            ),
            no_plano=op.node_id, operador=op.physical_op, tabelas=(no.simples,) if no.simples else (),
            colunas=tuple(sorted(_colunas_do_predicado(no.predicado))),
            detalhes={"linhas_lidas": lidas, "linhas_saida": saida, "cardinalidade": card, "fracao": fracao, "base": "real" if real else "estimado"},
        ))
    return achados


def _regra_p2(nos: list[_No], real: bool, lim: Limiares) -> list[Achado]:
    achados = []
    for no in nos:
        if not no.op.lookup:
            continue
        n = _execucoes(no, real)
        if n is None or n < lim.p2_execucoes_minimas:
            continue
        op = no.op
        leituras = f", {_n(op.actual_logical_reads)} leituras lógicas" if real else ""
        achados.append(Achado(
            regra="P2", antipadrao="AP3", gravidade=_gravidade_por_contagem(n, lim.p2_execucoes_altas), nivel="indicio",
            fontes=("plano",),
            evidencia=f"Nó {op.node_id} ({no.nome}, {op.physical_op}) em {no.tabela}: {_n(n)} {'execuções' if real else 'execuções estimadas (linhas do lado externo)'}{leituras} "
                      f"[{'plano real' if real else 'plano estimado'}]",
            explicacao=(
                "Para cada linha encontrada no índice, o servidor volta à tabela principal para buscar colunas que o índice não tem "
                "(Key Lookup). Com muitas linhas isso multiplica as leituras. Peça menos colunas ou inclua as necessárias no índice."
            ),
            no_plano=op.node_id, operador=no.nome, tabelas=(no.simples,) if no.simples else (),
            detalhes={"execucoes": n, "leituras_logicas": op.actual_logical_reads if real else None, "base": "real" if real else "estimado"},
        ))
    return achados


def _regra_p3(nos: list[_No], lim: Limiares) -> list[Achado]:
    achados = []
    for no in nos:
        if no.op.physical_op not in _VARREDURAS or no.predicado is None:
            continue
        usos = _funcoes_sobre_colunas(no.predicado)
        if not usos:
            continue
        op = no.op
        grande = (op.table_cardinality or 0) >= lim.p1_linhas_minimas
        explicitas = [u for u in usos if u.tipo != "conversao_implicita"]
        quem = "; ".join(f"{u.nome} sobre {u.tabela}.{u.coluna}" for u in usos)
        achados.append(Achado(
            regra="P3", antipadrao="AP1", gravidade=("alta" if explicitas else "media") if grande else "baixa", nivel="indicio", fontes=("plano",),
            evidencia=f"Nó {op.node_id} ({op.physical_op}) em {no.tabela}: predicado com {quem}; predicado: {_curto(usos[0].texto, 140)}"
                      + ("" if grande else " [tabela pequena]"),
            explicacao=(
                "O filtro da varredura aplica uma função ou conversão sobre a coluna; por isso o servidor precisa ler cada linha e "
                "calcular o valor antes de comparar, e um índice sobre a coluna não permitiria localizar as linhas diretamente. "
                + ("Quando é conversão implícita, os tipos da coluna e do valor comparado são diferentes: alinhe os tipos."
                   if not explicitas else "Compare a coluna diretamente com um valor ou intervalo.")
            ),
            no_plano=op.node_id, operador=op.physical_op, tabelas=(no.simples,) if no.simples else (),
            colunas=tuple(dict.fromkeys(u.coluna for u in usos)),
            detalhes={"usos": [asdict(u) for u in usos], "cardinalidade": op.table_cardinality},
        ))
    return achados


def _regra_p4(nos: list[_No], ins_avisos: list[ET.Element]) -> list[Achado]:
    achados = []
    fontes = [(None, w) for w in ins_avisos] + [(no, w) for no in nos for w in no.avisos]
    for no, w in fontes:
        itens = [(_tag(c), dict(c.attrib)) for c in w] + [(k, {"valor": v}) for k, v in w.attrib.items() if v in ("1", "true")]
        for nome, attrs in itens:
            onde = f"nó {no.op.node_id} ({no.op.physical_op})" if no is not None else "instrução"
            if nome == "PlanAffectingConvert":
                expr, problema = attrs.get("Expression", "?"), attrs.get("ConvertIssue", "?")
                refs = _colunas_da_expressao(expr)
                achados.append(Achado(
                    regra="P4", antipadrao="AP1", gravidade="alta" if problema == "Seek Plan" else "media", nivel="indicio", fontes=("plano",),
                    evidencia=f"Aviso PlanAffectingConvert em {onde}: ConvertIssue={problema}, expressão {_curto(expr, 140)}",
                    explicacao=(
                        f"O plano avisa que uma conversão de tipo ({problema}) afeta a escolha do plano. Costuma indicar tipos diferentes entre a "
                        "coluna e o valor comparado: "
                        + ("a conversão impede o uso de busca por índice (seek)." if problema == "Seek Plan" else "a estimativa de linhas fica menos confiável.")
                    ),
                    no_plano=no.op.node_id if no else None, operador=no.op.physical_op if no else None,
                    tabelas=tuple(dict.fromkeys(t for t, _ in refs)), colunas=tuple(dict.fromkeys(c for _, c in refs)),
                    detalhes={"convert_issue": problema, "expressao": expr},
                ))
            else:
                achados.append(Achado(
                    regra="P4", antipadrao=None, gravidade="baixa", nivel="indicio", fontes=("plano",),
                    evidencia=f"Aviso do plano em {onde}: {nome}" + (f" {attrs}" if attrs and nome != "valor" else ""),
                    explicacao="Aviso informativo emitido pelo otimizador; não corresponde a uma das quatro classes de antipadrão do catálogo.",
                    no_plano=no.op.node_id if no else None, operador=no.op.physical_op if no else None, detalhes={"aviso": nome, **attrs},
                ))
    return achados


def _regra_p5(ins: Instrucao, lim: Limiares) -> list[Achado]:
    achados = []
    for mi in ins.indices_ausentes:
        impacto = mi.get("impacto")
        if impacto is None or impacto < lim.p5_impacto_minimo:
            continue
        tabela = _sem_colchetes(mi["tabela"]) or ""
        cols = {k: [_sem_colchetes(c) for c in v] for k, v in mi["colunas"].items()}
        chave = cols.get("EQUALITY", []) + cols.get("INEQUALITY", [])
        incl = cols.get("INCLUDE", [])
        achados.append(Achado(
            regra="P5", antipadrao="AP4", gravidade="alta" if impacto >= lim.p5_impacto_alto else "media", nivel="indicio", fontes=("plano",),
            evidencia=f"MissingIndexes: índice em {tabela} (chave: {', '.join(chave) or '?'}"
                      + (f"; inclui: {', '.join(incl[:4])}" + (f" e mais {len(incl) - 4}" if len(incl) > 4 else "") if incl else "")
                      + f") com impacto estimado de {_n(impacto)}%",
            notas=(f"a sugestão inclui {len(incl)} colunas além da chave (cobre quase a tabela inteira; costuma vir de SELECT *)",) if len(incl) > 8 else (),
            explicacao=(
                "O próprio otimizador sugeriu um índice que reduziria o custo estimado. É uma sugestão isolada: não considera o custo de "
                "escrita nem índices parecidos já existentes, e pode listar colunas demais; a camada de análise decide."
            ),
            tabelas=(tabela.split(".")[-1],), colunas=tuple(chave + incl),
            detalhes={"impacto": impacto, "chave": chave, "incluidas": incl},
        ))
    return achados


def _regra_p6(nos: list[_No], real: bool, lim: Limiares) -> list[Achado]:
    achados = []
    for no in nos:
        if no.op.physical_op != "Nested Loops" or not no.correlacionado or len(no.filhos) < 2:
            continue
        interno = no.filhos[1]
        if interno.op.lookup:
            continue  # Key Lookup: tratado pela P2
        sub = list(_percorrer(interno))
        trabalho = [s for s in sub if s.op.logical_op == "Aggregate" or s.op.physical_op in _VARREDURAS or "Spool" in s.op.physical_op]
        n = _execucoes(interno, real)
        if not trabalho or n is None or n < lim.p6_execucoes_minimas:
            continue
        tabelas = tuple(dict.fromkeys(s.simples for s in sub if s.simples))
        ops = ", ".join(dict.fromkeys(f"{s.op.physical_op}" for s in trabalho))
        achados.append(Achado(
            regra="P6", antipadrao="AP2", gravidade=_gravidade_por_contagem(n, lim.p6_execucoes_altas), nivel="indicio",
            fontes=("plano",),
            evidencia=f"Nó {no.op.node_id} (Nested Loops, correlacionado): o lado interno (nó {interno.op.node_id}, {interno.op.physical_op}) roda "
                      f"{_n(n)} vezes [{'plano real' if real else 'plano estimado'}] e contém {ops}; tabelas do lado interno: {', '.join(tabelas) or '?'}",
            explicacao=(
                f"O servidor executa o lado interno {_n(n)} vezes, uma por linha do lado externo, e cada execução faz agregação ou varredura. "
                "É o formato típico de uma subconsulta correlacionada avaliada linha a linha."
            ),
            no_plano=no.op.node_id, operador="Nested Loops", tabelas=tabelas,
            detalhes={"execucoes_interno": n, "no_interno": interno.op.node_id, "operadores_de_trabalho": [s.op.physical_op for s in trabalho],
                      "base": "real" if real else "estimado"},
        ))
    return achados


def _analisar_plano(xml: str, lim: Limiares) -> tuple[list[Achado], str, list[str], set[str], str | None]:
    """(achados, 'real'|'estimado'|'invalido', avisos, tabelas do plano, motivo se invalido)."""
    try:
        pa = analisar_plano(xml)
        raiz = ET.fromstring(xml)
    except ET.ParseError as e:
        return [], "invalido", [], set(), f"XML invalido: {_curto(str(e), 160)}"
    ins = pa.principal()
    if ins is None or not ins.operadores:
        return [], "invalido", [], set(), "plano sem operadores (instrucao sem arvore de execucao)"
    avisos = []
    if sum(1 for i in pa.instrucoes if i.operadores) > 1:
        avisos.append("o plano tem mais de uma instrucao com operadores; so a primeira foi analisada")
    stmt = list(raiz.iter(NS + "StmtSimple"))[pa.instrucoes.index(ins)]
    qp = stmt.find(NS + "QueryPlan")
    raiz_relop = qp.find(NS + "RelOp") if qp is not None else None
    if raiz_relop is None:
        return [], "invalido", [], set(), "plano sem RelOp raiz"
    real = ins.eh_plano_real
    arvore = _construir(raiz_relop, {o.node_id: o for o in ins.operadores})
    nos = list(_percorrer(arvore))
    avisos_da_instrucao = [w for w in qp.findall(NS + "Warnings")]
    achados = (_regra_p1(nos, real, lim) + _regra_p2(nos, real, lim) + _regra_p3(nos, lim) + _regra_p4(nos, avisos_da_instrucao)
               + _regra_p5(ins, lim) + _regra_p6(nos, real, lim))
    return achados, "real" if real else "estimado", avisos, {n.simples.lower() for n in nos if n.simples}, None


# ----------------------------------------------------------------------------------------------- evidencia cruzada
def _cruzar(sql: list[Achado], plano: list[Achado], fatos: _FatosSql, tem_plano: bool) -> None:
    """Marca como confirmados os pares SQL x plano que concordam; acrescenta nota quando o plano nao corrobora."""
    def ligar(a: Achado, b: Achado) -> None:
        original = {a.id: a.evidencia, b.id: b.evidencia}   # evidencia de cada lado antes de acrescentar o par
        for x, y in ((a, b), (b, a)):
            x.nivel, x.fontes = "confirmado", ("sql", "plano")
            if y.id not in x.corrobora:
                x.corrobora += (y.id,)
                x.evidencia += f" | {'Plano' if y.regra[0] == 'P' else 'SQL'} concorda ({y.id}): {original[y.id]}"

    def mesma_tabela(a: Achado, b: Achado) -> bool:
        return bool({t.lower() for t in a.tabelas} & {t.lower() for t in b.tabelas})

    def mesma_coluna(s: Achado, p_cols: Iterable[str], p_tabs: Iterable[str]) -> bool:
        cols = {c.lower() for c in p_cols}
        tabs = {t.lower() for t in p_tabs}
        return any(c.lower() in cols and (not s.tabelas or {t.lower() for t in s.tabelas} & tabs) for c in s.colunas)

    for s in sql:
        if s.regra == "S1":   # SELECT * x Key Lookup nas tabelas do SELECT *
            for p in (p for p in plano if p.regra == "P2" and mesma_tabela(s, p)):
                ligar(s, p)
        elif s.regra == "S2":  # funcao no SQL x funcao/conversao no predicado da varredura ou aviso de conversao
            for p in plano:
                if p.regra == "P3" and mesma_coluna(s, p.colunas, p.tabelas):
                    ligar(s, p)
                elif p.regra == "P4" and p.antipadrao == "AP1" and any(
                        re.search(rf"\[{re.escape(c)}\]", p.detalhes.get("expressao", ""), re.I) for c in s.colunas):
                    ligar(s, p)
        elif s.regra == "S3":  # subconsulta correlacionada x Nested Loops com muitas execucoes no lado interno da mesma tabela
            for p in (p for p in plano if p.regra == "P6" and mesma_tabela(s, p)):
                ligar(s, p)
    for p in plano:  # AP4: a coluna do filtro (P1) ou da chave do indice sugerido (P5) tambem e filtro no SQL
        cols = {x.lower() for x in (p.detalhes.get("chave") if p.regra == "P5" else p.colunas) or ()}
        if p.regra in ("P1", "P5") and any((t is None or t.lower() in {x.lower() for x in p.tabelas}) and c in cols for t, c in fatos.filtros):
            p.nivel, p.fontes = "confirmado", ("sql", "plano")
            p.notas += ("confirmado porque a coluna do filtro/índice também é filtro no SQL (o SQL em si não é antipadrão)",)
    if tem_plano:
        notas = {
            "S1": "o plano não traz Key Lookup nesta tabela, então o custo das buscas extras não aparece aqui (o SELECT * continua lendo todas as colunas)",
            "S2": "o plano não mostra função/conversão sobre esta coluna em predicado de varredura (o otimizador pode ter simplificado a expressão)",
            "S3": "o plano não traz Nested Loops correlacionado com muitas execuções (o otimizador pode ter transformado a subconsulta em junção)",
        }
        for s in sql:
            if s.nivel == "indicio":
                s.notas += (notas[s.regra],)
        for p in plano:
            if p.regra == "P2" and p.nivel == "indicio":
                p.notas += ("o SQL não usa SELECT *: as colunas pedidas é que não estão todas no índice usado",) if fatos.tabelas else ()


# ----------------------------------------------------------------------------------------------- API
def diagnosticar(sql: str, plano_xml: str | None = None, limiares: Limiares | None = None) -> Diagnostico:
    """Diagnostico de uma consulta. Sem `plano_xml`, so as regras S1 a S3 rodam (e `regras_ignoradas` diz isso)."""
    lim = limiares or LIMIARES_PADRAO
    ach_sql, fatos, motivo_sql, avisos = _analisar_sql(sql, lim)
    ach_plano: list[Achado] = []
    tipo_plano, motivo_plano, tabelas_plano = "nenhum", "sem plano de execução fornecido", set()
    if plano_xml and plano_xml.strip():
        ach_plano, tipo_plano, avisos_plano, tabelas_plano, motivo_inv = _analisar_plano(plano_xml, lim)
        avisos += avisos_plano
        motivo_plano = f"plano inválido: {motivo_inv}" if tipo_plano == "invalido" else ""
        if tipo_plano != "invalido" and fatos.tabelas and tabelas_plano and not (fatos.tabelas & tabelas_plano):
            avisos.append("nenhuma tabela do plano aparece no SQL: o plano pode ser de outra consulta")
    ignoradas = {}
    if motivo_sql:
        ignoradas.update({r: f"SQL não analisável: {motivo_sql}" for r in ("S1", "S2", "S3")})
    if motivo_plano:
        ignoradas.update({r: motivo_plano for r in ("P1", "P2", "P3", "P4", "P5", "P6")})
    todos = sorted(ach_sql + ach_plano, key=lambda a: (_ORDEM_GRAVIDADE[a.gravidade], a.regra, a.linha_sql or 0, a.no_plano or 0))
    contagem: dict[str, int] = {}
    for a in todos:
        contagem[a.regra] = contagem.get(a.regra, 0) + 1
        a.id = f"{a.regra}-{contagem[a.regra]}"
    _cruzar([a for a in todos if a.regra[0] == "S"], [a for a in todos if a.regra[0] == "P"], fatos, tipo_plano in ("real", "estimado"))
    todos.sort(key=lambda a: (_ORDEM_GRAVIDADE[a.gravidade], 0 if a.confirmado else 1, a.regra, a.id))
    return Diagnostico(
        achados=todos, sql_analisavel=motivo_sql is None, motivo_sql=motivo_sql, plano=tipo_plano,
        regras_executadas=[r for r in REGRAS if r not in ignoradas], regras_ignoradas=ignoradas, avisos=avisos, limiares=lim,
    )


def diagnosticar_arquivos(arquivo_sql: str | Path, arquivo_plano: str | Path | None = None, limiares: Limiares | None = None) -> Diagnostico:
    plano = Path(arquivo_plano).read_text(encoding="utf-8") if arquivo_plano else None
    return diagnosticar(Path(arquivo_sql).read_text(encoding="utf-8"), plano, limiares)


def formatar(d: Diagnostico) -> str:
    """Relatorio em texto para terminal."""
    linhas = [f"Plano: {d.plano}. SQL analisavel: {'sim' if d.sql_analisavel else 'NAO (' + str(d.motivo_sql) + ')'}.",
              f"Regras executadas: {', '.join(d.regras_executadas) or '-'}"]
    for r, motivo in d.regras_ignoradas.items():
        linhas.append(f"  regra {r} NAO executada: {motivo}")
    linhas += [f"Aviso: {a}" for a in d.avisos]
    if not d.achados:
        linhas.append("Nenhum achado nas regras executadas (isto NAO prova ausencia de problemas nas regras ignoradas).")
    for a in d.achados:
        linhas.append(f"[{a.id}] {a.regra} {a.antipadrao or '-'} gravidade {a.gravidade} | {a.nivel} ({'+'.join(a.fontes)})")
        linhas.append(f"    evidência: {a.evidencia}")
        linhas.append(f"    explicação: {a.explicacao}")
        linhas += [f"    nota: {n}" for n in a.notas]
    return "\n".join(linhas)


def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Diagnostico de uma consulta T-SQL (offline: nao conecta ao SQL Server)")
    p.add_argument("--sql", required=True, help="arquivo com a consulta")
    p.add_argument("--plano", help="arquivo .sqlplan (real ou estimado)")
    p.add_argument("--json", action="store_true", help="saida em JSON")
    a = p.parse_args(argv)
    d = diagnosticar_arquivos(a.sql, a.plano)
    print(d.para_json() if a.json else formatar(d))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
