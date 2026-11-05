"""Prescricao (camada 3 do framework): transforma a analise em recomendacoes objetivas e tecnicamente justificadas.

Saida de `prescrever`:
  (a) DDL de indice (IDX-01) com a ordem das colunas justificada e o INCLUDE condicionado a projecao estreita;
  (b) CANDIDATAS de reescrita geradas com sqlglot (dialeto tsql):
        RW-01 funcao sobre coluna -> intervalo semiaberto (YEAR, MONTH+YEAR, DATEDIFF, CONVERT/CAST para date) ou igualdade
              direta (UPPER/LOWER com colacao insensivel a caixa);
        RW-02 subconsultas escalares correlacionadas (COUNT, MAX, MIN, SUM) sobre a mesma tabela e a mesma chave de
              igualdade -> JOIN + GROUP BY (LEFT JOIN; INNER quando o predicado ja descarta grupos vazios);
        RW-03 SELECT * -> lista de colunas, SOMENTE se o chamador informar as colunas necessarias;
  (c) recomendacoes em texto quando nao ha o que gerar com seguranca (ex.: reduzir a projecao, NAO indexar).

Toda reescrita nasce como "candidata nao verificada". So `verificar_candidatas(con, ...)` pode muda-la para
"equivalente_nos_dados" (mesmo hash de multiconjunto de linhas no banco consultado, via catalogo.verificar_equivalencia); isso
prova igualdade de resultado NAQUELES dados, nao equivalencia logica. Sem a verificacao nada aqui e apresentado como verificado.

Offline: nunca abre conexao por conta propria. Regras, limiares e estado de teste: knowledge/26-analise-e-prescricao.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .analise_indices import (
    LIMIARES_PADRAO, AnaliseIndices, ImpactoEscrita, Limiares, TabelaAnalisada, _resolver, analisar_indices, estimar_impacto_escrita,
)
from .catalogo import Consulta, ddl_reverter, verificar_equivalencia
from .diagnostico import _Fonte, _colunas_do_operando, _descendentes, _n, _pct
from .driver import Conexao
from .schema import EsquemaBanco, carregar

try:  # extra opcional `diagnostico` (sqlglot)
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.scope import build_scope
except ImportError:  # pragma: no cover
    sqlglot = None

STATUS_NAO_VERIFICADA = "nao_verificada"
STATUS_EQUIVALENTE = "equivalente_nos_dados"
_TIPOS_DATA = frozenset({"datetime", "datetime2", "smalldatetime", "date"})
_OPERADORES = {exp.EQ: "=", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="} if sqlglot else {}
_INVERSO = {"=": "=", ">": "<", ">=": "<=", "<": ">", "<=": ">="}


@dataclass(frozen=True)
class ConfigPrescricao:
    limiares: Limiares = LIMIARES_PADRAO
    colacao_assumida: str | None = "CI"   # usada por RW-01 (UPPER/LOWER) quando o esquema nao informa a colacao; None = nao assumir
    prefixo_indice: str = "IX_"


# ----------------------------------------------------------------------------------------------- estruturas
@dataclass
class ColunaChave:
    coluna: str
    papel: str                       # igualdade | juncao | intervalo
    sel_coluna: float | None = None
    obs: str = ""


@dataclass
class PrescricaoIndice:
    tabela: str
    decisao: str                     # propor | nao_propor
    causa: str                       # codigo da causa da analise que originou
    regra: str = "IDX-01"
    nome: str | None = None
    chave: list[ColunaChave] = field(default_factory=list)
    incluidas: list[str] = field(default_factory=list)
    ddl: str | None = None
    ddl_reverter: str | None = None
    cobre_consulta: bool | None = None
    justificativa_ordem: str = ""
    justificativa_include: str = ""
    justificativa: str = ""          # motivo geral (e, para nao_propor, o porque de nao indexar)
    ressalvas: list[str] = field(default_factory=list)
    evidencia: list[str] = field(default_factory=list)
    nao_determinado: list[str] = field(default_factory=list)
    impacto_escrita: ImpactoEscrita | None = None
    leituras_antes: float | None = None
    leituras_depois: float | None = None    # ESTIMATIVA; nao e medicao

    @property
    def colunas_chave(self) -> list[str]:
        return [c.coluna for c in self.chave]


@dataclass
class CandidataReescrita:
    regra: str                       # RW-01 | RW-02 | RW-03 | COMBINADA
    titulo: str
    sql_original: str
    sql_reescrito: str
    justificativa: str
    premissas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    trechos: list[str] = field(default_factory=list)    # "antes -> depois" de cada transformacao
    sql_equivalencia: str | None = None                  # referencia para comparar quando a projecao muda (RW-03)
    regras_aplicadas: list[str] = field(default_factory=list)
    status: str = STATUS_NAO_VERIFICADA                  # nao_verificada | equivalente_nos_dados | divergente | erro
    verificacao: dict | None = None
    detalhes: dict = field(default_factory=dict)

    @property
    def verificada(self) -> bool:
        return self.status == STATUS_EQUIVALENTE


@dataclass
class Recomendacao:
    regra: str
    texto: str
    justificativa: str


@dataclass
class Prescricao:
    indices: list[PrescricaoIndice]
    reescritas: list[CandidataReescrita]          # cada regra aplicada sozinha ao SQL original
    reescrita_final: CandidataReescrita | None    # todas as regras aplicaveis juntas (igual a unica candidata se so ha uma)
    recomendacoes: list[Recomendacao]
    nao_determinado: list[str]
    avisos: list[str]
    analise: AnaliseIndices | None = None

    def para_dict(self) -> dict:
        d = asdict(self)
        d.pop("analise", None)
        return d

    def para_json(self) -> str:
        return json.dumps(self.para_dict(), ensure_ascii=False, indent=2, default=str)

    def indice_proposto(self, tabela: str) -> PrescricaoIndice | None:
        return next((i for i in self.indices if i.decisao == "propor" and i.tabela.lower() == tabela.lower().split(".")[-1]), None)


# ----------------------------------------------------------------------------------------------- utilitarios de data
_RE_DATA = re.compile(r"^\s*(\d{4})-?(\d{2})-?(\d{2})(?:[ T].*)?$")


def _data_do_literal(x) -> date | None:
    """Data de um literal de texto 'YYYYMMDD' ou 'YYYY-MM-DD[ hh:mm:ss]' (descarta a hora); None se nao for data."""
    x = _desembrulhar(x)
    if not (isinstance(x, exp.Literal) and x.is_string):
        return None
    m = _RE_DATA.match(x.this)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _somar_meses(d: date, k: int) -> date:
    m = d.year * 12 + (d.month - 1) + k
    return date(m // 12, m % 12 + 1, 1)


def _texto_data(d: date) -> str:
    return d.strftime("%Y%m%d")


def _desembrulhar(x):
    """Tira parenteses e as normalizacoes internas do sqlglot (TsOrDsToDate, TimeStrToTime) em torno de coluna/literal."""
    while isinstance(x, (exp.Paren, exp.TsOrDsToDate, exp.TimeStrToTime)):
        x = x.this
    return x


def _coluna_de(x) -> "exp.Column | None":
    x = _desembrulhar(x)
    return x if isinstance(x, exp.Column) and not isinstance(x.this, exp.Star) else None


def _constante(x) -> bool:
    return not _colunas_do_operando(x)


def _lado_funcao(conj):
    """(no do lado da funcao, no constante, operador) normalizando para a funcao a esquerda; None se nao e comparacao simples."""
    if type(conj) not in _OPERADORES:
        return None
    op = _OPERADORES[type(conj)]
    a, b = conj.this, conj.expression
    while isinstance(a, exp.Paren):
        a = a.this
    while isinstance(b, exp.Paren):
        b = b.this
    if _colunas_do_operando(a) and _constante(b):
        return a, b, op
    if _colunas_do_operando(b) and _constante(a):
        return b, a, _INVERSO[op]
    return None


def _inteiro(x) -> int | None:
    x = _desembrulhar(x)
    if isinstance(x, exp.Literal) and not x.is_string:
        try:
            return int(x.this)
        except ValueError:
            return None
    if isinstance(x, exp.Neg):
        v = _inteiro(x.this)
        return -v if v is not None else None
    return None


def _conjuntos(no):
    while isinstance(no, exp.Paren):
        no = no.this
    if isinstance(no, exp.And):
        yield from _conjuntos(no.this)
        yield from _conjuntos(no.expression)
    else:
        yield no


def _lit(d: date) -> "exp.Literal":
    return exp.Literal.string(_texto_data(d))


def _intervalo(col, inicio: date | None, fim: date | None) -> list:
    """Conjuntos `col >= inicio` e/ou `col < fim` (semiaberto)."""
    out = []
    if inicio is not None:
        out.append(exp.GTE(this=col.copy(), expression=_lit(inicio)))
    if fim is not None:
        out.append(exp.LT(this=col.copy(), expression=_lit(fim)))
    return out


# ----------------------------------------------------------------------------------------------- RW-01
@dataclass
class _Troca:
    funcao: str
    coluna: str
    qualificador: str
    tabela: str | None
    antes: str
    depois: str
    tipo_pos: str                    # igualdade | intervalo : o que o predicado passa a ser
    premissas: list[str] = field(default_factory=list)


def _colacao_ci(esquema: EsquemaBanco | None, tabela: str | None, coluna: str, cfg: ConfigPrescricao) -> tuple[bool | None, str]:
    """(insensivel a caixa?, premissa/motivo). True/False vem do esquema; None = nao determinado e nao assumido."""
    nome = None
    if esquema is not None and tabela:
        c = esquema.coluna(tabela, coluna)
        nome = c.colacao if c else None
    nome = nome or (esquema.colacao_banco if esquema is not None else None)
    if nome:
        if "_CI_" in nome.upper():
            return True, f"colação {nome} (insensível a caixa, informada pelo esquema)"
        if "_CS_" in nome.upper() or "_BIN" in nome.upper():
            return False, f"colação {nome} é sensível a caixa"
    if cfg.colacao_assumida == "CI":
        return True, "colação ASSUMIDA insensível a caixa (o esquema não informa a colação; confirme com SELECT DATABASEPROPERTYEX(DB_NAME(), 'Collation'))"
    return None, "colação não informada e nenhuma assumida"


def _tipo_data_ok(esquema: EsquemaBanco | None, tabela: str | None, coluna: str) -> tuple[bool | None, str]:
    if esquema is None or not tabela or esquema.coluna(tabela, coluna) is None:
        return None, f"tipo de {coluna} ASSUMIDO como data/hora (sem esquema para confirmar)"
    t = esquema.coluna(tabela, coluna).tipo.lower()
    return (t in _TIPOS_DATA), f"{coluna} é {t}"


def _trocas_de_conjunto(conjs: list, escopo, esquema: EsquemaBanco | None, cfg: ConfigPrescricao, avisos: list[str], fonte=None):
    """Para a lista de conjuntos de um WHERE/ON devolve (novos conjuntos, trocas). Combina YEAR+MONTH da mesma coluna."""
    info = []   # (conj, lado, const, op)
    for c in conjs:
        r = _lado_funcao(c)
        info.append((c, *r) if r else (c, None, None, None))
    anos, meses = {}, {}
    for i, (c, f, k, op) in enumerate(info):
        if isinstance(f, exp.Year) and op == "=" and _inteiro(k) is not None and _coluna_de(f.this) is not None:
            anos[_coluna_de(f.this).sql().lower()] = i
        elif isinstance(f, exp.Month) and op == "=" and _inteiro(k) is not None and _coluna_de(f.this) is not None:
            meses[_coluna_de(f.this).sql().lower()] = i
    consumidos: set[int] = set()
    novos: list = []
    trocas: list[_Troca] = []

    def tabela_da(col):
        t, _ = _resolver(escopo, col, esquema)
        return t.name if t is not None else None

    def data_ok(col, premissas) -> bool:
        tab = tabela_da(col)
        ok, msg = _tipo_data_ok(esquema, tab, col.name)
        if ok is False:
            avisos.append(f"RW-01 não aplicada a {col.sql(dialect='tsql')}: {msg}; o intervalo só vale para tipos de data/hora")
            return False
        if ok is None:
            premissas.append(msg)
        return True

    for i, (c, f, k, op) in enumerate(info):
        if i in consumidos:
            continue
        tr = fonte.trecho(c) if fonte is not None else None
        texto_antes = tr[0] if tr else c.sql(dialect="tsql")
        premissas: list[str] = []
        subst = None     # (lista de conjuntos novos, funcao, coluna, tipo_pos, consumidos extras)
        # --- YEAR (+ MONTH)
        if isinstance(f, exp.Year) and _coluna_de(f.this) is not None and _inteiro(k) is not None:
            col = _coluna_de(f.this)
            ano = _inteiro(k)
            if not (1753 <= ano <= 9998):
                avisos.append(f"RW-01 não aplicada a {texto_antes}: ano fora do intervalo seguro para datetime (1753 a 9998)")
            elif data_ok(col, premissas):
                j = meses.get(col.sql().lower())
                if op == "=" and j is not None and j != i:
                    mes = _inteiro(info[j][2])
                    if mes is not None and 1 <= mes <= 12:
                        ini = date(ano, mes, 1)
                        subst = (_intervalo(col, ini, _somar_meses(ini, 1)), "YEAR+MONTH", col, "intervalo", {j},
                                 f"{texto_antes} AND {(fonte.trecho(info[j][0]) or (info[j][0].sql(dialect='tsql'),))[0] if fonte is not None else info[j][0].sql(dialect='tsql')}")
                if subst is None:
                    d1, d2 = date(ano, 1, 1), date(ano + 1, 1, 1)
                    novo = {"=": _intervalo(col, d1, d2), ">": _intervalo(col, d2, None), ">=": _intervalo(col, d1, None),
                            "<": _intervalo(col, None, d1), "<=": _intervalo(col, None, d2)}[op]
                    subst = (novo, "YEAR", col, "intervalo", set(), texto_antes)
        # --- DATEDIFF(unidade, a, b) = k
        elif isinstance(f, exp.DateDiff) and op == "=" and _inteiro(k) is not None:
            unidade = (f.args.get("unit").name.upper() if f.args.get("unit") is not None else "")
            fim_c, ini_c = _coluna_de(f.this), _coluna_de(f.expression)
            lit_d = _data_do_literal(f.expression) if fim_c is not None else (_data_do_literal(f.this) if ini_c is not None else None)
            if unidade in ("DAY", "MONTH", "YEAR") and lit_d is not None and (fim_c is None) != (ini_c is None):
                col, kk = (fim_c, _inteiro(k)) if fim_c is not None else (ini_c, -_inteiro(k))
                # DATEDIFF(u, lit, col) = k  <=>  periodo(col) = periodo(lit) + k ; DATEDIFF(u, col, lit) = k  <=>  periodo(col) = periodo(lit) - k
                base = {"DAY": lit_d, "MONTH": date(lit_d.year, lit_d.month, 1), "YEAR": date(lit_d.year, 1, 1)}[unidade]
                try:
                    if unidade == "DAY":
                        ini = date.fromordinal(base.toordinal() + kk)
                        fim = date.fromordinal(ini.toordinal() + 1)
                    elif unidade == "MONTH":
                        ini, fim = _somar_meses(base, kk), _somar_meses(base, kk + 1)
                    else:
                        ini, fim = date(base.year + kk, 1, 1), date(base.year + kk + 1, 1, 1)
                except (ValueError, OverflowError):
                    ini = None
                if ini is not None and ini.year >= 1753 and fim.year <= 9999 and data_ok(col, premissas):
                    subst = (_intervalo(col, ini, fim), f"DATEDIFF({unidade.lower()})", col, "intervalo", set(), texto_antes)
                    premissas.append("DATEDIFF conta fronteiras de período entre as duas datas; o intervalo reproduz essa contagem para o período do literal")
        # --- CONVERT(date, col) / CAST(col AS date) com literal de data
        elif f is not None and ((isinstance(f, exp.Convert) and f.this is not None and f.this.is_type("date") and f.args.get("style") is None
                                 and _coluna_de(f.expression) is not None)
                                or (isinstance(f, exp.Cast) and f.to.is_type("date") and _coluna_de(f.this) is not None)):
            col = _coluna_de(f.expression if isinstance(f, exp.Convert) else f.this)
            d = _data_do_literal(k)
            if d is not None and data_ok(col, premissas):
                prox = date.fromordinal(d.toordinal() + 1)
                novo = {"=": _intervalo(col, d, prox), ">": _intervalo(col, prox, None), ">=": _intervalo(col, d, None),
                        "<": _intervalo(col, None, d), "<=": _intervalo(col, None, prox)}[op]
                subst = (novo, "CONVERT/CAST(date)", col, "intervalo", set(), texto_antes)
        # --- UPPER / LOWER com colacao insensivel a caixa
        elif isinstance(f, (exp.Upper, exp.Lower)) and op == "=" and _coluna_de(f.this) is not None:
            col = _coluna_de(f.this)
            lit_n = _desembrulhar(k)
            valor = lit_n.this if isinstance(lit_n, (exp.Literal, exp.National)) and (not isinstance(lit_n, exp.Literal) or lit_n.is_string) else None
            alvo = "UPPER" if isinstance(f, exp.Upper) else "LOWER"
            if valor is None:
                avisos.append(f"RW-01 não aplicada a {texto_antes}: o valor comparado não é um literal de texto")
            else:
                coerente = valor == (valor.upper() if alvo == "UPPER" else valor.lower())
                ci, msg = _colacao_ci(esquema, tabela_da(col), col.name, cfg)
                if not coerente:
                    avisos.append(f"RW-01 não aplicada a {texto_antes}: o literal não está em {'maiúsculas' if alvo == 'UPPER' else 'minúsculas'}, "
                                  f"então a comparação original nunca é verdadeira e a troca por igualdade direta mudaria o resultado")
                elif ci is None or ci is False:
                    avisos.append(f"RW-01 não aplicada a {texto_antes}: {msg}")
                else:
                    premissas.append(msg)
                    subst = ([exp.EQ(this=col.copy(), expression=k.copy())], alvo, col, "igualdade", set(),
                             texto_antes)
                    premissas.append(f"{alvo}(col) = literal equivale a col = literal sob colação insensível a caixa, exceto em casos de borda do mapeamento de caixa "
                                     "(caracteres especiais Unicode); por isso a candidata exige a verificação de resultado")
        if subst is None:
            if isinstance(c, (exp.Or, exp.Not)) and any(isinstance(n, (exp.Year, exp.Month, exp.DateDiff, exp.Upper, exp.Lower, exp.Convert, exp.Cast))
                                                        and _colunas_do_operando(n) for n in c.find_all(exp.Func)):
                avisos.append(f"RW-01 não aplicada a {texto_antes}: a função está dentro de OR/NOT; reescreva manualmente cada ramo")
            novos.append(c)
            continue
        lista, nome_f, col, tipo_pos, extras, antes = subst
        consumidos |= extras
        novos.extend(lista)
        depois = " AND ".join(x.sql(dialect="tsql") for x in lista)
        trocas.append(_Troca(nome_f, col.name, col.table or "", tabela_da(col), antes, depois, tipo_pos, premissas))
    # os consumidos (MONTH) nao entram: refaz a lista preservando a ordem
    if consumidos:
        novos = [x for x in novos if not any(x is info[j][0] for j in consumidos)]
    return novos, trocas


def _origens(sel):
    """(holder, tipo, raiz) de WHERE e de cada JOIN ... ON do SELECT."""
    w = sel.args.get("where")
    if w is not None:
        yield w, "this", w.this
    for j in sel.args.get("joins") or []:
        if j.args.get("on") is not None:
            yield j, "on", j.args["on"]


def aplicar_rw01(arvore, esquema: EsquemaBanco | None, cfg: ConfigPrescricao, fonte=None) -> tuple[list[_Troca], list[str]]:
    """Aplica RW-01 IN PLACE em `arvore` (use uma copia). Devolve as trocas feitas e os avisos de casos nao aplicados."""
    raiz = build_scope(arvore)
    trocas: list[_Troca] = []
    avisos: list[str] = []
    mudancas = []
    if raiz is None:
        return trocas, avisos
    for escopo in raiz.traverse():
        sel = escopo.expression
        if not isinstance(sel, exp.Select):
            continue
        for holder, chave, predicado in _origens(sel):
            conjs = list(_conjuntos(predicado))
            novos, t = _trocas_de_conjunto(conjs, escopo, esquema, cfg, avisos, fonte)
            if t:
                mudancas.append((holder, chave, novos))
                trocas += t
    for holder, chave, novos in mudancas:
        holder.set(chave, exp.and_(*[n.copy() for n in novos], copy=False) if novos else exp.true())
    # MONTH sozinho e outras funcoes de data sem reescrita segura: aviso para o usuario
    for escopo in raiz.traverse():
        sel = escopo.expression
        if isinstance(sel, exp.Select) and sel.args.get("where") is not None:
            for c in _conjuntos(sel.args["where"].this):
                r = _lado_funcao(c)
                if r and isinstance(r[0], exp.Month):
                    avisos.append(f"RW-01 não aplicada a {c.sql(dialect='tsql')}: MONTH(coluna) sozinho não corresponde a um intervalo contíguo "
                                  "(todos os meses de todos os anos); some YEAR(coluna) ou use uma tabela de datas")
    return trocas, avisos


# ----------------------------------------------------------------------------------------------- RW-02
_NOME_CLAUSULA = {"limit": "TOP/LIMIT", "group": "GROUP BY", "order": "ORDER BY", "joins": "JOIN", "windows": "WINDOW", "with_": "WITH", "with": "WITH"}


@dataclass
class _Sub:
    no: object                        # exp.Subquery
    agg: object                       # Count | Max | Min | Sum
    tabela: object                    # exp.Table
    alias: str
    corr: list                        # [(coluna interna, coluna externa)]
    extras: list                      # conjuntos so da tabela interna
    forma: str                        # select | where
    conj: object | None = None
    op: str | None = None
    const: object | None = None


def _fonte_unica(sel) -> "tuple[exp.Table, str] | None":
    """(Table, alias) quando o SELECT tem exatamente uma tabela base na FROM e nenhuma juncao."""
    de = sel.args.get("from_") or sel.args.get("from")
    if de is None or sel.args.get("joins") or not isinstance(de.this, exp.Table):
        return None
    t = de.this
    return t, (t.alias or t.name)


def _eh_agregado_suportado(x) -> bool:
    return isinstance(x, (exp.Count, exp.Max, exp.Min, exp.Sum))


def _analisar_sub(sq, alias_ext: str, tab_ext: "exp.Table", esquema: EsquemaBanco | None) -> "_Sub | str":
    """Devolve a descricao da subconsulta ou o motivo (texto) de nao ser reescrevivel."""
    inner = sq.this
    if not isinstance(inner, exp.Select):
        return "subconsulta não é um SELECT simples"
    if len(inner.expressions) != 1 or not _eh_agregado_suportado(inner.expressions[0]):
        return "a subconsulta não devolve um único agregado COUNT, MAX, MIN ou SUM"
    for chave in ("group", "having", "distinct", "limit", "order", "joins", "with_", "with", "qualify", "windows"):
        if inner.args.get(chave):
            return f"a subconsulta usa {_NOME_CLAUSULA.get(chave, chave.upper())}: fora do padrão suportado"
    if any(isinstance(n, exp.Select) for n in _descendentes(inner, lambda f: False) if n is not inner):
        return "a subconsulta contém outra subconsulta"
    fu = _fonte_unica(inner)
    if fu is None:
        return "a subconsulta não lê uma única tabela base"
    tab, alias = fu
    if inner.args.get("where") is None:
        return "a subconsulta não tem WHERE (não há correlação)"
    corr, extras = [], []
    for c in _conjuntos(inner.args["where"].this):
        cols = _colunas_do_operando(c)
        # quais colunas sao da tabela interna?
        def eh_interna(col) -> bool | None:
            if col.table:
                if col.table.lower() == alias.lower():
                    return True
                if col.table.lower() == alias_ext.lower():
                    return False
                return None
            if esquema is not None:
                i, e = esquema.coluna(tab.name, col.name) is not None, esquema.coluna(tab_ext.name, col.name) is not None
                if i:
                    return True
                if e:
                    return False
            return None
        classes = [eh_interna(col) for col in cols]
        if any(x is None for x in classes):
            return "coluna sem qualificador (ou de outra tabela) na subconsulta: sem esquema não dá para saber a quem pertence"
        if all(classes):
            extras.append(c)
            continue
        if isinstance(c, exp.EQ) and len(cols) == 2 and isinstance(c.this, exp.Column) and isinstance(c.expression, exp.Column) and sorted(classes) == [False, True]:
            ci, ce = (c.this, c.expression) if eh_interna(c.this) else (c.expression, c.this)
            corr.append((ci, ce))
            continue
        return "a correlação não é por igualdade simples entre uma coluna da subconsulta e uma coluna da consulta externa"
    if not corr:
        return "nenhuma igualdade de correlação encontrada"
    agg = inner.expressions[0]
    for col in _colunas_do_operando(agg):
        if col.table and col.table.lower() != alias.lower():
            return "o agregado usa coluna da consulta externa"
    return _Sub(sq, agg, tab, alias, corr, extras, "select")


def _descarta_vazio(agg, op: str, const) -> bool:
    """O predicado `agregado <op> constante` e falso/desconhecido para o grupo vazio (COUNT = 0; MAX/MIN/SUM = NULL)?"""
    if not isinstance(_desembrulhar(const), (exp.Literal, exp.Neg)):
        return False
    if isinstance(agg, exp.Count):
        v = _inteiro(const)
        if v is None:
            return False
        return not {"=": 0 == v, ">": 0 > v, ">=": 0 >= v, "<": 0 < v, "<=": 0 <= v}[op]
    return True    # comparacao com NULL nunca e verdadeira


def _renomear_alias(no, antigo: str, novo: str) -> None:
    for c in list(no.find_all(exp.Column)):
        if c.table and c.table.lower() == antigo.lower():
            c.set("table", exp.to_identifier(novo))


def aplicar_rw02(arvore, esquema: EsquemaBanco | None) -> tuple["exp.Expression | None", list[str], list[str], list[str]]:
    """RW-02 sobre uma copia: devolve (nova arvore | None, motivos de recusa, premissas, trechos antes->depois)."""
    motivos: list[str] = []
    if not isinstance(arvore, exp.Select):
        return None, ["a instrução não é um SELECT simples (UNION, DML ou outro): RW-02 não aplicada"], [], []
    sel = arvore
    fu = _fonte_unica(sel)
    if fu is None:
        return None, ["a consulta externa não lê uma única tabela base sem junções: a reescrita multiplicaria linhas ou exigiria derivadas"], [], []
    tab_ext, alias_ext = fu
    for chave in ("group", "having", "distinct", "limit", "windows", "qualify"):
        if sel.args.get(chave):
            return None, [f"a consulta externa usa {_NOME_CLAUSULA.get(chave, chave.upper())}: RW-02 não aplicada"], [], []
    if any(isinstance(n, exp.AggFunc) and n.find_ancestor(exp.Subquery) is None for n in sel.find_all(exp.AggFunc)):
        return None, ["a consulta externa já tem agregação: RW-02 não aplicada"], [], []
    if sel.find(exp.Window) is not None:
        return None, ["a consulta externa usa função de janela: RW-02 não aplicada"], [], []
    if any(isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)) for e in sel.expressions):
        return None, ["a consulta externa usa SELECT *: com GROUP BY seria preciso listar as colunas (aplique RW-03 antes)"], [], []
    # subconsultas candidatas
    subs: list[_Sub] = []
    for item in sel.expressions:
        for sq in item.find_all(exp.Subquery):
            if sq.find_ancestor(exp.Select) is not sel or isinstance(sq.parent, (exp.Exists, exp.In)):
                continue
            r = _analisar_sub(sq, alias_ext, tab_ext, esquema)
            if isinstance(r, str):
                motivos.append(r)
            else:
                subs.append(r)
    if sel.args.get("where") is not None:
        for c in _conjuntos(sel.args["where"].this):
            lf = _lado_funcao_subconsulta(c)
            if lf is None:
                continue
            sq, const, op = lf
            r = _analisar_sub(sq, alias_ext, tab_ext, esquema)
            if isinstance(r, str):
                motivos.append(r)
            else:
                r.forma, r.conj, r.op, r.const = "where", c, op, const
                subs.append(r)
    if not subs:
        return None, motivos or ["nenhuma subconsulta escalar correlacionada reescrevível"], [], []
    # grupos: mesma tabela, mesma correlacao, mesmos filtros adicionais
    def chave_grupo(s: _Sub):
        return (s.tabela.name.lower(), tuple(sorted((a.name.lower(), b.sql().lower()) for a, b in s.corr)), tuple(sorted(x.sql().lower() for x in s.extras)))
    grupos: dict[tuple, list[_Sub]] = {}
    for s in subs:
        grupos.setdefault(chave_grupo(s), []).append(s)
    maior = max(grupos.values(), key=len)
    if len(grupos) > 1:
        motivos.append("há subconsultas sobre tabelas/correlações/filtros diferentes: só o maior grupo foi reescrito; juntar grupos diferentes multiplicaria "
                       "as linhas da junção (exigiria pré-agregação), por isso os demais ficaram como subconsulta")
    g = maior
    modelo = g[0]
    # unicidade da chave externa: o GROUP BY precisa conter uma chave unica da tabela externa
    if esquema is None or esquema.buscar_tabela(tab_ext.name) is None:
        return None, motivos + ["sem esquema: não foi possível confirmar que o GROUP BY preserva uma linha por linha externa (unicidade da chave)"], [], []
    # novo SELECT (copia ja recebida): substituir as subconsultas do grupo
    todos_where = all(s.forma == "where" for s in g)
    inner_ok = todos_where and all(_descarta_vazio(s.agg, s.op, s.const) for s in g)
    alias_novo = modelo.alias
    usados = {alias_ext.lower()}
    if alias_novo.lower() in usados:
        alias_novo += "_i"
    pk = None
    for chave in esquema.chaves_unicas(modelo.tabela.name):
        if len(chave) == 1:
            col = esquema.coluna(modelo.tabela.name, chave[0])
            if col is not None and not col.anulavel:
                pk = chave[0]
                break
    nao_nulo = pk or modelo.corr[0][0].name

    def novo_agregado(s: _Sub):
        a = s.agg.copy()
        _renomear_alias(a, s.alias, alias_novo)
        if isinstance(a, exp.Count) and isinstance(a.this, exp.Star):
            if inner_ok:
                return a
            a = exp.Count(this=exp.column(nao_nulo, table=alias_novo))
        return a

    trechos: list[str] = []
    for s in g:
        antes = s.no.sql(dialect="tsql")
        if s.forma == "select":
            novo = novo_agregado(s)
            s.no.replace(novo)
            trechos.append(f"{antes} -> {novo.sql(dialect='tsql')}")
    having = []
    removidos = set()
    for s in g:
        if s.forma == "where":
            a = novo_agregado(s)
            having.append(type(s.conj)(this=a, expression=s.const.copy()) if s.conj.this is s.no or _desembrulhar(s.conj.this) is s.no
                          else type(s.conj)(this=s.const.copy(), expression=a))
            trechos.append(f"{s.conj.sql(dialect='tsql')} -> HAVING {having[-1].sql(dialect='tsql')}")
            removidos.add(id(s.conj))
    # WHERE restante
    if sel.args.get("where") is not None:
        restantes = [c for c in _conjuntos(sel.args["where"].this) if id(c) not in removidos]
        if restantes:
            sel.set("where", exp.Where(this=exp.and_(*[c.copy() for c in restantes], copy=False)))
        else:
            sel.set("where", None)
    # JOIN
    cond = [exp.EQ(this=ci.copy(), expression=ce.copy()) for ci, ce in modelo.corr] + [x.copy() for x in modelo.extras]
    for c in cond:
        _renomear_alias(c, modelo.alias, alias_novo)
    tabela_in = modelo.tabela.copy()
    tabela_in.set("alias", exp.TableAlias(this=exp.to_identifier(alias_novo)))
    junta = exp.Join(this=tabela_in, on=exp.and_(*cond, copy=False))
    if not inner_ok:
        junta.set("side", "LEFT")
    sel.set("joins", (sel.args.get("joins") or []) + [junta])
    # GROUP BY: colunas externas fora de agregados (SELECT, ORDER BY) + colunas externas da correlacao
    grupo: list = []
    vistos: set[str] = set()

    def juntar(col):
        k = col.sql().lower()
        if k not in vistos:
            vistos.add(k)
            grupo.append(col.copy())

    nomes_select = {e.alias.lower() for e in sel.expressions if isinstance(e, exp.Alias)}
    for e in sel.expressions:
        for col in e.find_all(exp.Column):
            if isinstance(col.this, exp.Star) or col.find_ancestor(exp.AggFunc) is not None:
                continue
            if col.find_ancestor(exp.Select) is not sel and (col.table or "").lower() != alias_ext.lower():
                continue   # dentro de uma subconsulta que ficou: so as colunas da tabela externa entram no GROUP BY
            juntar(col)
    ordem = sel.args.get("order")
    if ordem is not None:
        for col in ordem.find_all(exp.Column):
            if col.find_ancestor(exp.AggFunc) is None and not (not col.table and col.name.lower() in nomes_select):
                juntar(col)
    for _, ce in modelo.corr:
        juntar(ce)
    chaves = esquema.chaves_unicas(tab_ext.name)
    nomes_grupo = {c.name.lower() for c in grupo}
    if not any(k and all(c.lower() in nomes_grupo for c in k) for k in chaves):
        return None, motivos + [f"o GROUP BY ({', '.join(c.sql() for c in grupo)}) não contém uma chave única de {tab_ext.name}: agruparia linhas externas distintas"], [], []
    sel.set("group", exp.Group(expressions=grupo))
    if having:
        sel.set("having", exp.Having(this=exp.and_(*having, copy=False)))
    premissas = [
        f"{tab_ext.name}: o GROUP BY contém uma chave única (confirmada no esquema), então continua havendo uma linha por linha externa",
        ("valor para linha externa sem correspondentes: COUNT = 0 (contando " + f"{alias_novo}.{nao_nulo}, coluna não nula nas linhas que casam), MAX/MIN/SUM = NULL, igual ao da subconsulta"
         if not inner_ok else "COUNT(*) sobre a junção interna conta as linhas que casam, igual ao COUNT(*) da subconsulta"),
        ("INNER JOIN: o predicado do WHERE descarta o grupo vazio (COUNT = 0 ou NULL comparado com constante nunca é verdadeiro), então não se perde linha" if inner_ok
         else "LEFT JOIN: mantém as linhas externas sem correspondentes, como a subconsulta"),
    ]
    return arvore, motivos, premissas, trechos


def _lado_funcao_subconsulta(conj):
    """(Subquery, constante, operador) de `(subconsulta) <op> constante` (ou invertido) no WHERE; None caso contrario."""
    if type(conj) not in _OPERADORES and not isinstance(conj, exp.NEQ):
        return None
    op = _OPERADORES.get(type(conj), "<>")
    a, b = conj.this, conj.expression
    while isinstance(a, exp.Paren):
        a = a.this
    while isinstance(b, exp.Paren):
        b = b.this
    if isinstance(a, exp.Subquery) and _constante(b) and not isinstance(b, exp.Subquery):
        return a, b, op
    if isinstance(b, exp.Subquery) and _constante(a) and not isinstance(a, exp.Subquery):
        return b, a, _INVERSO.get(op, op)
    return None


# ----------------------------------------------------------------------------------------------- RW-03
def aplicar_rw03(arvore, esquema: EsquemaBanco | None, colunas: "list[str] | dict[str, list[str]]") -> tuple[bool, list[str], list[str]]:
    """SELECT * -> lista de colunas, IN PLACE. Devolve (aplicada, avisos, trechos). So com colunas informadas pelo chamador."""
    avisos, trechos = [], []
    if not isinstance(arvore, exp.Select):
        return False, ["RW-03: a instrução não é um SELECT simples"], trechos
    tabelas = [f for f in [(arvore.args.get("from_") or arvore.args.get("from")).this if (arvore.args.get("from_") or arvore.args.get("from")) else None]
               + [j.this for j in arvore.args.get("joins") or []] if isinstance(f, exp.Table)]
    estrelas = [e for e in arvore.expressions if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))]
    if not estrelas:
        return False, ["RW-03: o SELECT externo não tem * na projeção"], trechos
    if isinstance(colunas, dict):
        cols = [c for lista in colunas.values() for c in lista]
    else:
        cols = list(colunas)
    if not cols:
        return False, ["RW-03: lista de colunas vazia"], trechos
    if esquema is not None and len(tabelas) == 1 and esquema.buscar_tabela(tabelas[0].name) is not None:
        ruins = [c for c in cols if esquema.coluna(tabelas[0].name, c.split(".")[-1]) is None]
        if ruins:
            return False, [f"RW-03 não aplicada: coluna(s) {', '.join(ruins)} inexistente(s) em {tabelas[0].name}"], trechos
    if len(tabelas) > 1 and any("." not in c for c in cols):
        return False, ["RW-03 não aplicada: há mais de uma tabela; informe as colunas qualificadas pelo alias (ex.: p.Id)"], trechos
    novas = [sqlglot.parse_one(c, read="tsql", into=exp.Column) if "." in c else exp.column(c) for c in cols]
    if len(estrelas) > 1 or isinstance(estrelas[0], exp.Column):
        return False, ["RW-03 não aplicada: t.* ou mais de um * (informe só um SELECT * simples)"], trechos
    antes = estrelas[0].sql(dialect="tsql")
    idx = arvore.expressions.index(estrelas[0])
    arvore.set("expressions", arvore.expressions[:idx] + novas + arvore.expressions[idx + 1:])
    trechos.append(f"{antes} -> {', '.join(c.sql(dialect='tsql') for c in novas)}")
    return True, avisos, trechos


def _sql_equivalencia_rw03(original, colunas) -> tuple[str | None, str | None]:
    """`SELECT colunas FROM (original sem ORDER BY) AS t`: a referencia para comparar quando a projecao muda."""
    cols = [c.split(".")[-1] for c in (colunas if not isinstance(colunas, dict) else [x for v in colunas.values() for x in v])]
    ref = original.copy()
    if isinstance(ref, exp.Select) and ref.args.get("order") is not None and not ref.args.get("limit"):
        ref.set("order", None)
    elif isinstance(ref, exp.Select) and ref.args.get("order") is not None:
        return None, "o original tem ORDER BY com TOP: não é possível usá-lo como tabela derivada para a equivalência"
    de = (ref.args.get("from_") or ref.args.get("from"))
    if ref.args.get("joins") or (de is not None and not isinstance(de.this, exp.Table)):
        return None, "o original tem junções: nomes de colunas repetidos impedem a tabela derivada; compare a projeção manualmente"
    return f"SELECT {', '.join(cols)} FROM ({ref.sql(dialect='tsql')}) AS t", None


# ----------------------------------------------------------------------------------------------- reescritas (orquestracao)
def _parse(sql: str):
    try:
        arvs = [a for a in sqlglot.parse(sql, read="tsql") if a is not None]
    except (SqlglotError, RecursionError):
        return None
    return arvs[0] if len(arvs) == 1 and arvs[0].find(exp.Select) else None


def _formatar_sql(arvore) -> str:
    return arvore.sql(dialect="tsql", pretty=True)


def _tem_subconsulta_escalar_correlacionada(arvore) -> bool:
    """Ha subconsulta escalar com coluna qualificada da consulta externa (fora de EXISTS, IN, ANY, ALL, APPLY e FROM)?"""
    raiz = build_scope(arvore)
    if raiz is None:
        return False
    for escopo in raiz.traverse():
        if not escopo.is_subquery:
            continue
        pai = escopo.expression.parent
        if isinstance(pai, (exp.Exists, exp.Lateral, exp.Join, exp.From, exp.Table)) or (
                isinstance(pai, exp.Subquery) and isinstance(pai.parent, (exp.In, exp.Any, exp.All, exp.Lateral, exp.Join, exp.From, exp.Table))):
            continue
        if [c for c in escopo.external_columns if c.table]:
            return True
    return False


def gerar_reescritas(sql: str, esquema: EsquemaBanco | None, cfg: ConfigPrescricao, colunas_necessarias=None
                     ) -> tuple[list[CandidataReescrita], CandidataReescrita | None, list[_Troca], list[str], list[Recomendacao]]:
    """Candidatas RW-01/02/03 (cada uma sobre o SQL original) e a combinada. Nunca verificadas aqui."""
    cands: list[CandidataReescrita] = []
    recs: list[Recomendacao] = []
    avisos: list[str] = []
    original = _parse(sql)
    if original is None:
        return [], None, [], ["SQL não analisável (ParseError ou mais de uma instrução): nenhuma reescrita gerada"], recs
    aviso_gerador = "SQL regerado pelo sqlglot (dialeto tsql): a formatação muda e algumas expressões (ex.: DATEDIFF) podem ser escritas de forma equivalente; compare com o original"
    ordem_regras = []
    # RW-01
    a1 = original.copy()
    try:
        fonte = _Fonte(sql)
    except Exception:   # tokenizacao falhou: usa o SQL regerado nos trechos
        fonte = None
    trocas, av1 = aplicar_rw01(a1, esquema, cfg, fonte)
    avisos += av1
    if trocas:
        prem = list(dict.fromkeys(p for t in trocas for p in t.premissas))
        cands.append(CandidataReescrita(
            "RW-01", "função sobre coluna -> predicado sargável", sql, _formatar_sql(a1),
            "Aplicar função ou conversão à coluna obriga o servidor a calculá-la linha a linha e impede a busca por intervalo ou igualdade em índice sobre a coluna "
            "(antipadrão 1, knowledge/03 §2.3). Comparar a coluna diretamente com limites equivalentes preserva o resultado e a torna sargável.",
            prem, [aviso_gerador], [f"{t.antes} -> {t.depois}" for t in trocas], regras_aplicadas=["RW-01"],
            detalhes={"trocas": [asdict(t) for t in trocas]},
        ))
        ordem_regras.append("RW-01")
    # RW-02
    a2 = original.copy()
    res2, motivos2, prem2, trechos2 = aplicar_rw02(a2, esquema)
    if res2 is not None:
        cands.append(CandidataReescrita(
            "RW-02", "subconsultas escalares correlacionadas -> JOIN + GROUP BY", sql, _formatar_sql(res2),
            "Cada subconsulta escalar correlacionada é logicamente reavaliada por linha externa; com várias subconsultas sobre a mesma tabela o JOIN com agregação "
            "lê a tabela interna uma vez (antipadrão 2, knowledge/03 §2.3; no catálogo, o ganho só aparece com 2 ou mais subconsultas, knowledge/23 §2.2).",
            prem2, [aviso_gerador] + (["ganho não garantido com uma única subconsulta: o otimizador pode já tê-la convertido em junção (caso S5 do catálogo, sem mudança de leituras) "
                                       "e, com índice, a reescrita pode ler mais que só o índice (caso S4); veja knowledge/26, seção 5"] if len(trechos2) == 1 else []),
            trechos2, regras_aplicadas=["RW-02"],
        ))
        ordem_regras.append("RW-02")
    elif motivos2 and _tem_subconsulta_escalar_correlacionada(original):
        recs.append(Recomendacao("RW-02", "Subconsulta correlacionada: reescrita automática não gerada. Considere reescrevê-la como JOIN + GROUP BY manualmente.",
                                 "; ".join(dict.fromkeys(motivos2))))
    if res2 is not None and motivos2:
        avisos += [f"RW-02 parcial: {m}" for m in motivos2]
    # RW-03
    estrela = original.find(exp.Star) is not None and any(
        isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
        for s in original.find_all(exp.Select) for e in s.expressions)
    if estrela and not colunas_necessarias:
        recs.append(Recomendacao("RW-03", "SELECT *: reduza a projeção às colunas que a aplicação usa. Informe-as (colunas_necessarias) para gerar a reescrita e o INCLUDE do índice.",
                                 "SELECT * impede que um índice com poucas colunas cubra a consulta e obriga Key Lookup por linha (antipadrão 3, knowledge/03 §2.3). "
                                 "Só quem conhece a aplicação sabe quais colunas são necessárias, por isso nenhuma reescrita é gerada sem essa informação."))
    elif estrela and colunas_necessarias:
        a3 = original.copy()
        ok, av3, trechos3 = aplicar_rw03(a3, esquema, colunas_necessarias)
        if ok:
            eq, motivo_eq = _sql_equivalencia_rw03(original, colunas_necessarias)
            c3 = CandidataReescrita(
                "RW-03", "SELECT * -> lista de colunas", sql, _formatar_sql(a3),
                "Listar só as colunas necessárias permite que um índice com INCLUDE as cubra e reduz o volume lido e transferido (antipadrão 3, knowledge/03 §2.3).",
                ["as colunas informadas pelo chamador são exatamente as que a aplicação usa (não verificável pela ferramenta)"], [aviso_gerador], trechos3,
                sql_equivalencia=eq, regras_aplicadas=["RW-03"],
            )
            if motivo_eq:
                c3.avisos.append("equivalência: " + motivo_eq)
            cands.append(c3)
            ordem_regras.append("RW-03")
        else:
            avisos += av3
    # combinada
    final = None
    if len(cands) == 1:
        final = cands[0]
    elif len(cands) > 1:
        comb = original.copy()
        trocas_c, _ = aplicar_rw01(comb, esquema, cfg)
        r2, _, _, _ = aplicar_rw02(comb, esquema)
        comb = r2 if r2 is not None else comb
        if estrela and colunas_necessarias:
            aplicar_rw03(comb, esquema, colunas_necessarias)
        final = CandidataReescrita(
            "COMBINADA", "todas as regras aplicáveis juntas (" + ", ".join(ordem_regras) + ")", sql, _formatar_sql(comb),
            " | ".join(c.justificativa for c in cands), list(dict.fromkeys(p for c in cands for p in c.premissas)), [aviso_gerador],
            [t for c in cands for t in c.trechos], regras_aplicadas=list(ordem_regras),
            sql_equivalencia=next((c.sql_equivalencia for c in cands if c.sql_equivalencia), None),
        )
    return cands, final, trocas, avisos, recs


# ----------------------------------------------------------------------------------------------- IDX-01
def _papel_da_coluna(col: str, preds, tipo_pos: dict[str, str]) -> str | None:
    tipos = {p.tipo for p in preds if p.coluna.lower() == col.lower()}
    if "igualdade" in tipos or tipo_pos.get(col.lower()) == "igualdade":
        return "igualdade"
    if "juncao" in tipos:
        return "juncao"
    if "intervalo" in tipos or tipo_pos.get(col.lower()) == "intervalo":
        return "intervalo"
    return None


def _ordenar(cols: list[str], sel_col: dict[str, float | None]) -> tuple[list[str], str]:
    """Mais seletiva primeiro (mais valores distintos / total); sem densidade para todas, mantem a ordem do SQL."""
    if len(cols) < 2:
        return cols, ""
    if all(sel_col.get(c.lower()) is not None for c in cols):
        return sorted(cols, key=lambda c: -sel_col[c.lower()]), "ordenadas pela seletividade da coluna (distintos/total), a mais seletiva primeiro: " + ", ".join(
            f"{c} {_pct(sel_col[c.lower()])}" for c in sorted(cols, key=lambda c: -sel_col[c.lower()]))
    return cols, "ordem do SQL mantida: a seletividade de todas as colunas (densidade de estatística) não está disponível, então não há como ordenar por seletividade"


def _nome_indice(prefixo: str, tabela: str, chave: list[str], cobrindo: bool) -> str:
    base = f"{prefixo}{tabela}_{'_'.join(chave)}" + ("_cob" if cobrindo else "")
    return re.sub(r"[^0-9A-Za-z_]", "", base)[:128]


def _prescrever_indice(t: TabelaAnalisada, analise: AnaliseIndices, esquema: EsquemaBanco | None, tipo_pos: dict[str, dict[str, str]],
                       cfg: ConfigPrescricao) -> PrescricaoIndice | None:
    lim = cfg.limiares
    causa = next((c for c in t.causas if c.codigo in ("indice_ausente", "baixa_seletividade", "ordem_colunas_inadequada", "indice_nao_cobre")), None)
    if causa is None:
        return None
    preds = analise.predicados_de(t.tabela)
    ev = list(causa.evidencia)
    if causa.codigo == "baixa_seletividade":
        return PrescricaoIndice(
            t.tabela, "nao_propor", causa.codigo, justificativa=(
                "Não indexar: " + (causa.detalhes.get("criterio_seletividade") or "seletividade baixa") +
                (". Reduza a projeção (RW-03) e reavalie: um índice que cobre a consulta custa menos que a varredura mesmo com seletividade baixa." if t.select_estrela and not t.colunas_informadas else ".")),
            evidencia=ev, nao_determinado=list(causa.nao_determinado), leituras_antes=causa.detalhes.get("leituras_varredura"),
            leituras_depois=causa.detalhes.get("leituras_estimadas_com_indice"))
    tp = tipo_pos.get(t.tabela.lower(), {})
    cols = list(dict.fromkeys(causa.colunas))
    papeis = {c: _papel_da_coluna(c, preds, tp) for c in cols}
    sem_papel = [c for c, p in papeis.items() if p is None]
    cols = [c for c in cols if papeis[c] is not None]
    nd = list(causa.nao_determinado)
    ressalvas: list[str] = []
    if sem_papel:
        ressalvas.append(f"coluna(s) {', '.join(sem_papel)} com função sem reescrita segura: fora da chave (o predicado continua não sargável)")
    if not cols:
        return PrescricaoIndice(t.tabela, "nao_propor", causa.codigo, justificativa="Nenhuma coluna do filtro pode ser usada em busca (predicados não sargáveis sem reescrita).",
                                evidencia=ev, nao_determinado=nd)
    sel_col = {c.lower(): (esquema.seletividade_coluna(t.tabela, c) if esquema else None) for c in cols}
    eq = [c for c in cols if papeis[c] == "igualdade"]
    jn = [c for c in cols if papeis[c] == "juncao"]
    rg = [c for c in cols if papeis[c] == "intervalo"]
    eq, j_eq = _ordenar(eq, sel_col)
    jn, j_jn = _ordenar(jn, sel_col)
    residuais: list[str] = []
    if len(rg) > 1:
        pref = max(rg, key=lambda c: sel_col.get(c.lower()) or 0.0) if all(sel_col.get(c.lower()) is not None for c in rg) else rg[0]
        residuais = [c for c in rg if c != pref]
        rg = [pref]
        ressalvas.append(f"mais de uma coluna de intervalo: só {pref} entra na chave; {', '.join(residuais)} fica(m) como filtro residual (INCLUDE)")
    chave = [ColunaChave(c, "igualdade", sel_col.get(c.lower())) for c in eq] + [ColunaChave(c, "juncao", sel_col.get(c.lower())) for c in jn] + \
            [ColunaChave(c, "intervalo", sel_col.get(c.lower())) for c in rg]
    nomes_chave = [c.coluna for c in chave]
    partes = []
    if eq:
        partes.append(f"igualdade(s) do filtro primeiro ({', '.join(eq)}" + (f"; {j_eq}" if j_eq else "") + ")")
    if jn and eq:
        partes.append(f"depois a(s) coluna(s) de junção ({', '.join(jn)}" + (f"; {j_jn}" if j_jn else "") + "), porque o predicado constante restringe antes que a junção forneça valores [SUPOSIÇÃO]")
    elif jn:
        partes.append(f"coluna(s) de junção ({', '.join(jn)}" + (f"; {j_jn}" if j_jn else "") + "): é por ela(s) que a tabela é acessada a partir da outra")
    if rg:
        partes.append(f"por último uma só coluna de intervalo ({rg[0]}): colunas depois de uma de intervalo não participam da busca")
    justificativa_ordem = "Ordem da chave: " + "; ".join(partes) + "."
    if len(chave) > 1:
        justificativa_ordem += (" Base: o índice composto só é plenamente aproveitado se o filtro respeitar a ordem das colunas (knowledge/03 §2.2); "
                                "medido na R1 (V4, experiment_id 18): (OwnerUserId, Score) 11 leituras lógicas contra 825 de (Score, OwnerUserId).")
    # INCLUDE
    clust = esquema.indice_clusterizado(t.tabela) if esquema else None
    ja = {c.lower() for c in nomes_chave} | {c.lower() for c in (clust.chave if clust else ())}
    necessarias = [c for c in t.colunas_lidas if c.lower() not in ja]
    incl: list[str] = []
    cobre: bool | None = None
    if t.select_estrela and not t.colunas_informadas:
        justificativa_include = "Sem INCLUDE: a consulta usa SELECT *; primeiro reduza a projeção (RW-03) e informe as colunas necessárias para então incluí-las."
        cobre = False
        ressalvas.append("SELECT *: o índice só tem a chave; cada linha achada exige Key Lookup. Medido na R1 (C1): chave sozinha leu 46.157 páginas, contra 41 com a projeção reduzida e INCLUDE")
    elif esquema is None or esquema.buscar_tabela(t.tabela) is None:
        justificativa_include = "Sem INCLUDE: sem esquema não é possível confirmar que as colunas lidas são estreitas (tipos desconhecidos)."
        cobre = None if necessarias else True
        if necessarias:
            nd.append(f"colunas lidas fora da chave ({', '.join(necessarias)}) não incluídas: tipos desconhecidos (sem esquema)")
    else:
        largas = [c for c in necessarias if (esquema.coluna(t.tabela, c) and esquema.coluna(t.tabela, c).bytes == -1)]
        if largas:
            justificativa_include = f"Sem INCLUDE: {', '.join(largas)} é de tipo max (índice enorme e pouco ganho)."
            cobre = False
        elif len(necessarias) > lim.include_max_colunas:
            justificativa_include = f"Sem INCLUDE: {len(necessarias)} colunas além da chave (máximo {lim.include_max_colunas})."
            cobre = False
        else:
            incl = necessarias
            cobre = True
            justificativa_include = (f"INCLUDE ({', '.join(incl)}): as demais colunas lidas pela consulta, projeção estreita ({len(incl)} coluna(s), nenhuma de tipo max); "
                                     "o índice passa a cobrir a consulta e elimina os Key Lookups." if incl else
                                     "Sem INCLUDE: a consulta só lê a chave (e a chave do clusterizado, que o índice já carrega); o índice já cobre a consulta.")
        if not cobre:
            ressalvas.append("índice não cobre a consulta: haverá Key Lookup por linha encontrada; vale só se o filtro for bem seletivo (contraexemplo medido na R1: S1, JOIN + índice só na chave leu 4,96 milhões de páginas contra 2,41 milhões do original)")
    if t.colunas_informadas:
        ressalvas.append("INCLUDE baseado nas colunas informadas pelo chamador (a ferramenta não consegue verificar que são as usadas pela aplicação)")
    if causa.codigo == "indice_nao_cobre":
        ressalvas.append(f"já existe {causa.detalhes.get('indice')} com esta chave: o índice proposto o amplia com INCLUDE; remova o antigo só depois de validar")
    if causa.codigo == "ordem_colunas_inadequada":
        ressalvas.append(f"já existe {causa.detalhes.get('indice')} com as colunas em outra ordem: o proposto tem a ordem do filtro; o antigo pode ser removido se nenhuma outra consulta o usa (não determinado aqui)")
    if causa.detalhes.get("condicionada_a_reescrita"):
        ressalvas.append("o índice só é aproveitado depois da reescrita RW-01 (o predicado original, com função na coluna, continua não sargável)")
    if causa.detalhes.get("ressalva_baixa_sel"):
        ressalvas.append("seletividade baixa pelo limiar: o ganho vem de ler uma estrutura mais estreita que a tabela (índice cobrindo), não de buscar poucas linhas")
    nome = _nome_indice(cfg.prefixo_indice, t.tabela, nomes_chave, bool(incl))
    sch = t.esquema_tabela or (esquema.buscar_tabela(t.tabela).esquema if esquema and esquema.buscar_tabela(t.tabela) else None)
    alvo = f"{sch}.{t.tabela}" if sch else t.tabela
    ddl = f"CREATE NONCLUSTERED INDEX {nome} ON {alvo} ({', '.join(nomes_chave)})" + (f" INCLUDE ({', '.join(incl)})" if incl else "")
    impacto = estimar_impacto_escrita(esquema, t.tabela, nomes_chave, incl, lim)
    return PrescricaoIndice(
        t.tabela, "propor", causa.codigo, nome=nome, chave=chave, incluidas=incl, ddl=ddl, ddl_reverter=ddl_reverter(ddl), cobre_consulta=cobre,
        justificativa_ordem=justificativa_ordem, justificativa_include=justificativa_include,
        justificativa=f"Causa (camada ②): {causa.codigo}. " + (causa.detalhes.get("criterio_seletividade") or ""),
        ressalvas=ressalvas, evidencia=ev, nao_determinado=nd, impacto_escrita=impacto,
        leituras_antes=causa.detalhes.get("leituras_varredura"), leituras_depois=causa.detalhes.get("leituras_estimadas_com_indice"),
    )


def _fundir_indices(propostas: list[PrescricaoIndice], cfg: ConfigPrescricao, esquema: EsquemaBanco | None) -> list[PrescricaoIndice]:
    """Propostas com a mesma tabela e a mesma chave viram uma so, com a uniao do INCLUDE (se couber no limite)."""
    saida: list[PrescricaoIndice] = []
    for p in propostas:
        if p.decisao != "propor":
            saida.append(p)
            continue
        igual = next((q for q in saida if q.decisao == "propor" and q.tabela.lower() == p.tabela.lower() and [c.lower() for c in q.colunas_chave] == [c.lower() for c in p.colunas_chave]), None)
        if igual is None:
            saida.append(p)
            continue
        uniao = list(dict.fromkeys(igual.incluidas + p.incluidas))
        if len(uniao) <= cfg.limiares.include_max_colunas:
            igual.incluidas = uniao
            igual.ddl = re.sub(r"\s+INCLUDE \(.*\)$", "", igual.ddl) + (f" INCLUDE ({', '.join(uniao)})" if uniao else "")
            igual.ddl_reverter = ddl_reverter(igual.ddl)
            igual.impacto_escrita = estimar_impacto_escrita(esquema, igual.tabela, igual.colunas_chave, uniao, cfg.limiares)
        igual.evidencia += [e for e in p.evidencia if e not in igual.evidencia]
    return saida


# ----------------------------------------------------------------------------------------------- API
def prescrever(
    sql: str, plano_xml: str | None = None, esquema: EsquemaBanco | None = None, colunas_necessarias=None,
    config: ConfigPrescricao | None = None, analise: AnaliseIndices | None = None,
) -> Prescricao:
    """Prescricao completa de uma consulta: indices (IDX-01) e candidatas de reescrita (RW-01..03). Offline.

    `colunas_necessarias`: so o chamador sabe quais colunas a aplicacao usa quando a consulta tem SELECT *; sem isso RW-03 e so texto e
    o indice nao recebe INCLUDE. `analise`: reaproveita uma analise ja feita (precisa ter sido feita com as mesmas colunas)."""
    cfg = config or ConfigPrescricao()
    if sqlglot is None:
        raise RuntimeError("prescrever exige o sqlglot (extra `diagnostico`)")
    if analise is None:
        analise = analisar_indices(sql, plano_xml, esquema, cfg.limiares, colunas_necessarias=colunas_necessarias)
    cands, final, trocas, avisos, recs = gerar_reescritas(sql, esquema, cfg, colunas_necessarias)
    tipo_pos: dict[str, dict[str, str]] = {}
    for t in trocas:
        if t.tabela:
            tipo_pos.setdefault(t.tabela.lower(), {})[t.coluna.lower()] = t.tipo_pos
    propostas = [p for t in analise.tabelas if (p := _prescrever_indice(t, analise, esquema, tipo_pos, cfg)) is not None]
    indices = _fundir_indices(propostas, cfg, esquema)
    nd = list(analise.nao_determinado)
    if not analise.sql_analisavel:
        avisos.append(f"SQL não analisável pela camada ②: {analise.motivo_sql}")
    if not cands and not indices and not recs:
        recs.append(Recomendacao("-", "Nenhuma prescrição gerada.", "Nenhuma causa de índice nem antipadrão reescrevível foi encontrado nas condições analisadas; veja 'não determinado'."))
    for t in analise.tabelas:
        for c in t.causas:
            if c.codigo == "predicado_nao_sargavel" and not any(x.regra == "RW-01" for x in cands):
                recs.append(Recomendacao("RW-01", f"Predicado não sargável em {t.tabela}.{', '.join(c.colunas)}: reescrita automática não gerada; compare a coluna diretamente com um valor ou intervalo.",
                                         "; ".join(dict.fromkeys(a for a in avisos if a.startswith("RW-01"))) or "padrão de função não coberto pelas regras RW-01 (YEAR, MONTH+YEAR, DATEDIFF, CONVERT/CAST date, UPPER/LOWER)"))
    return Prescricao(indices, cands, final, recs, nd, avisos, analise)


def verificar_candidatas(con: Conexao, prescricao: Prescricao, sql_original: str) -> Prescricao:
    """EXECUTA no banco da `con` o original e cada candidata (e a combinada) e compara o hash de multiconjunto das linhas
    (`catalogo.verificar_equivalencia`). So aqui uma candidata deixa de ser "nao_verificada". O resultado vale para os dados
    do banco consultado, uma execucao; nao prova equivalencia logica. NAO chamar com o banco ocupado por uma coleta."""
    alvos = list(prescricao.reescritas) + ([prescricao.reescrita_final] if prescricao.reescrita_final and prescricao.reescrita_final not in prescricao.reescritas else [])
    for c in alvos:
        consulta = Consulta(id=c.regra, antipadrao="-", titulo=c.titulo, tabelas=[], seletividade="-", sql_original=sql_original,
                            sql_reescrita=c.sql_reescrito, sql_equivalencia=c.sql_equivalencia)
        try:
            r = verificar_equivalencia(con, consulta)
        except Exception as e:   # erro de SQL na candidata, timeout etc.: nunca vira "verificada"
            c.status, c.verificacao = "erro", {"erro": str(e)[:300]}
            continue
        c.verificacao = r
        c.status = STATUS_EQUIVALENTE if r.get("ok") else "divergente"
    return prescricao


# ----------------------------------------------------------------------------------------------- saida
def formatar(p: Prescricao) -> str:
    out = []
    for i in p.indices:
        if i.decisao == "propor":
            out.append(f"[{i.regra}] ÍNDICE em {i.tabela} (causa: {i.causa})")
            out.append(f"    {i.ddl}")
            out.append(f"    reverter: {i.ddl_reverter}")
            out.append(f"    {i.justificativa_ordem}")
            out.append(f"    {i.justificativa_include}")
            if i.impacto_escrita:
                im = i.impacto_escrita
                out.append(f"    escrita [{im.rotulo}]: índices da tabela {im.indices_antes} -> {im.indices_depois}; chave {im.largura_chave_bytes} B; "
                           f"linha do índice {im.largura_linha_bytes} B; ~{_n(im.paginas_estimadas)} páginas (~{im.mb_estimados} MB); colunas atualizáveis na chave/INCLUDE: {im.colunas_chave_atualizaveis}")
            if i.leituras_antes is not None and i.leituras_depois is not None:
                out.append(f"    leituras lógicas: varredura {_n(round(i.leituras_antes))} -> ~{_n(round(i.leituras_depois))} [ESTIMATIVA, não medida]")
        else:
            out.append(f"[{i.regra}] NÃO INDEXAR {i.tabela} (causa: {i.causa}): {i.justificativa}")
        out += [f"    ressalva: {r}" for r in i.ressalvas]
        out += [f"    NÃO determinado: {r}" for r in i.nao_determinado]
    for c in p.reescritas + ([p.reescrita_final] if p.reescrita_final and p.reescrita_final not in p.reescritas else []):
        rot = "VERIFICADA (resultado igual no banco consultado)" if c.verificada else ("CANDIDATA NÃO VERIFICADA" if c.status == STATUS_NAO_VERIFICADA else f"candidata: verificação = {c.status}")
        out.append(f"[{c.regra}] {c.titulo} -- {rot}")
        out += [f"    {t}" for t in c.trechos]
        out.append("    " + c.sql_reescrito.replace("\n", "\n    "))
        out.append(f"    justificativa: {c.justificativa}")
        out += [f"    premissa: {x}" for x in c.premissas] + [f"    aviso: {x}" for x in c.avisos]
    out += [f"[{r.regra}] {r.texto} ({r.justificativa})" for r in p.recomendacoes]
    out += [f"Aviso: {a}" for a in p.avisos] + [f"NÃO determinado: {x}" for x in p.nao_determinado]
    return "\n".join(out)


def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Prescricao (camada 3). Offline: nao conecta ao SQL Server")
    ap.add_argument("--sql", required=True)
    ap.add_argument("--plano")
    ap.add_argument("--esquema")
    ap.add_argument("--colunas", help="colunas necessarias (SELECT *), separadas por virgula")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cols = [c.strip() for c in a.colunas.split(",")] if a.colunas else None
    p = prescrever(Path(a.sql).read_text(encoding="utf-8"), Path(a.plano).read_text(encoding="utf-8") if a.plano else None,
                   carregar(a.esquema) if a.esquema else None, cols)
    print(p.para_json() if a.json else formatar(p))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
