"""Analise de indices (camada 2 do framework): cruza a consulta e o plano com a estrutura de indices existente.

Entrada: o texto T-SQL, o plano XML (real ou estimado; opcional) e o `EsquemaBanco` (opcional; ver schema.py). Saida:
`AnaliseIndices`, com, por predicado de filtro ou juncao: tabela, coluna, tipo (igualdade, intervalo, funcao sobre a
coluna, juncao), seletividade do PREDICADO lida do plano (linhas do operador / cardinalidade da tabela; marcada como
estimada ou real), seletividade da COLUNA no sentido do projeto (distintos / total; so quando o esquema traz densidade,
senao None), indices existentes que a cobrem; e, por tabela, a CAUSA classificada:
  indice_ausente | predicado_nao_sargavel | indice_nao_cobre (key lookup) | ordem_colunas_inadequada |
  subconsulta_correlacionada_repetida | baixa_seletividade (recomendacao: NAO indexar) | sem_problema | indeterminada
Cada causa traz a evidencia (no do plano, trecho do SQL) e o que NAO pode ser determinado. Tambem estima, de forma
ESTATICA, o impacto de escrita de um indice proposto (`estimar_impacto_escrita`): rotulado como estimativa.

Offline: nunca abre conexao. Regras, limiares e estado de teste: knowledge/26-analise-e-prescricao.md.
Dependencia opcional: sqlglot (extra `diagnostico`); sem ele o resultado diz que o SQL nao foi analisado.
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from math import ceil
from pathlib import Path

from .diagnostico import Diagnostico, _colunas_do_predicado, _construir, _curto, _execucoes, _n, _pct, _percorrer, _tag
from .plano import NS, analisar_plano
from .schema import EsquemaBanco, carregar, chave_tabela

try:  # extra opcional `diagnostico`
    import sqlglot
    from sqlglot import exp
    from sqlglot.errors import SqlglotError
    from sqlglot.optimizer.scope import build_scope

    from .diagnostico import _Fonte, _aplica_funcao, _colunas_do_operando, _nomes_de_funcao
except ImportError:  # pragma: no cover - ambiente sem o extra
    sqlglot = None

CAUSAS = (
    "indice_ausente", "predicado_nao_sargavel", "indice_nao_cobre", "ordem_colunas_inadequada",
    "subconsulta_correlacionada_repetida", "baixa_seletividade", "sem_problema", "indeterminada",
)
TIPOS_PREDICADO = ("igualdade", "intervalo", "funcao", "juncao", "outro")
_VARREDURAS = ("Table Scan", "Clustered Index Scan", "Index Scan")
_TIPOS_VARIAVEIS = frozenset({"varchar", "nvarchar", "varbinary", "xml", "text", "ntext", "image", "sql_variant"})


# ----------------------------------------------------------------------------------------------- limiares
@dataclass(frozen=True)
class Limiares:
    """Parametros das camadas 2 e 3. TODOS sao SUPOSICOES, salvo onde o comentario diz que foi ajustado a medicoes."""
    sel_baixa: float = 0.20                  # predicado que devolve > 20% da tabela tem seletividade "baixa" (limite media/baixa do catalogo)
    tabela_minima_linhas: int = 10_000       # abaixo disso a tabela e pequena demais para justificar indice
    include_max_colunas: int = 5             # INCLUDE so se a projecao tiver no maximo N colunas alem da chave
    regra_literal_seletividade: bool = False  # True: so o limiar sel_baixa decide (regra literal do projeto); False: tambem o custo estimado (doc 26, secao 3.3)
    lookup_paginas_por_linha: float = 4.0    # paginas lidas por linha achada via indice NAO cobrindo; calibrado em 2 medicoes da R1 (C1 e contraexemplo de S1: 4,07 e 4,01)
    descida_indice_paginas: int = 3          # paginas da descida da raiz ate a folha (suposicao)
    lookup_execucoes_minimas: int = 1_000    # execucoes de Key Lookup para falar em "indice nao cobre"
    # modelo estatico de tamanho de indice (ajustado a 12 indices medidos na R1, ver doc 26, secao 2.4; colunas de tamanho variavel: suposicao)
    pagina_util_bytes: int = 8096
    cabecalho_linha_bytes: int = 4
    slot_bytes: int = 2
    fracao_media_variavel: float = 0.5       # sem comprimento medio medido, coluna variavel ocupa metade do tamanho declarado
    fator_niveis_superiores: float = 1.003   # niveis nao folha do indice sobre as paginas folha
    ponteiro_lob_bytes: int = 24             # coluna (max) incluida no indice: ponteiro (suposicao, nao medido)


LIMIARES_PADRAO = Limiares()


# ----------------------------------------------------------------------------------------------- estruturas
@dataclass
class Predicado:
    id: str
    tabela: str                          # nome simples, como escrito no SQL
    esquema_tabela: str | None
    coluna: str
    tipo: str                            # um de TIPOS_PREDICADO
    operador: str | None                 # =, >, BETWEEN, IN, LIKE ...
    clausula: str                        # WHERE | JOIN ... ON | subconsulta (correlacao)
    trecho_sql: str | None
    linha_sql: int | None
    funcao: str | None = None            # YEAR, UPPER, CONVERT/CAST ... quando tipo == funcao
    outra_coluna: str | None = None      # juncao: o outro lado (u.Id)
    escopo: int = 0                      # 0 = consulta externa; 1.. = subconsultas/CTEs na ordem do sqlglot
    no_plano: int | None = None
    operador_plano: str | None = None
    cardinalidade: float | None = None
    sel_estimada: float | None = None    # linhas estimadas do operador / cardinalidade
    sel_real: float | None = None        # linhas reais do operador / cardinalidade (so plano real)
    conjunta: bool = False               # o operador avalia mais de um predicado: a fracao e a da conjuncao, nao deste predicado
    sel_coluna: float | None = None      # distintos / total (densidade do esquema); None sem densidade
    indices_que_cobrem: list[str] = field(default_factory=list)   # indices existentes cuja 1a coluna-chave e esta coluna
    evidencia: list[str] = field(default_factory=list)
    nao_determinado: list[str] = field(default_factory=list)

    @property
    def sel_predicado(self) -> float | None:
        """Melhor fracao disponivel: a real (plano real) vence a estimada."""
        return self.sel_real if self.sel_real is not None else self.sel_estimada

    @property
    def base_sel(self) -> str | None:
        return "real" if self.sel_real is not None else ("estimada" if self.sel_estimada is not None else None)


@dataclass
class IndiceVisto:
    nome: str
    tipo: str                            # CLUSTERED | NONCLUSTERED
    chave: tuple[str, ...]
    incluidas: tuple[str, ...] = ()
    origem: str = "esquema"              # esquema | plano (so o que o plano mostrou)
    completo: bool = True                # False: chave conhecida so em parte (indice visto so no plano)


@dataclass
class Causa:
    codigo: str                          # um de CAUSAS
    tabela: str | None
    colunas: tuple[str, ...]
    recomendacao: str                    # criar_indice | criar_indice_cobrindo | nao_indexar | reescrever_predicado | reescrever_subconsulta |
    #                                      ajustar_indice | cobrir_indice_ou_reduzir_projecao | investigar | nenhuma
    evidencia: list[str]
    nao_determinado: list[str] = field(default_factory=list)
    detalhes: dict = field(default_factory=dict)


@dataclass
class TabelaAnalisada:
    tabela: str
    esquema_tabela: str | None
    linhas: int | None
    paginas: int | None
    indices: list[IndiceVisto]
    colunas_lidas: list[str]             # colunas da tabela citadas em qualquer parte da consulta
    select_estrela: bool
    projecao_estreita: bool | None       # cabe em INCLUDE (sem *, sem tipo max, <= include_max_colunas)? None = nao determinado
    predicados: list[str]                # ids
    causas: list[Causa]
    subconsultas_correlacionadas: int = 0
    nos_plano: list[int] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)
    colunas_informadas: bool = False     # SELECT * com as colunas necessarias informadas pelo chamador (colunas_lidas as incluem)

    @property
    def causa_principal(self) -> Causa | None:
        return self.causas[0] if self.causas else None


@dataclass
class AnaliseIndices:
    predicados: list[Predicado]
    tabelas: list[TabelaAnalisada]
    sql_analisavel: bool
    motivo_sql: str | None
    plano: str                           # nenhum | estimado | real | invalido
    esquema_fornecido: bool
    limiares: Limiares
    avisos: list[str] = field(default_factory=list)
    nao_determinado: list[str] = field(default_factory=list)   # o que a analise, como um todo, nao pode afirmar

    def tabela(self, nome: str) -> TabelaAnalisada | None:
        n = nome.lower().replace("[", "").replace("]", "").split(".")[-1]
        return next((t for t in self.tabelas if t.tabela.lower() == n), None)

    def predicados_de(self, nome: str) -> list[Predicado]:
        n = nome.lower().split(".")[-1]
        return [p for p in self.predicados if p.tabela.lower() == n]

    def para_dict(self) -> dict:
        return asdict(self)

    def para_json(self) -> str:
        return json.dumps(self.para_dict(), ensure_ascii=False, indent=2, default=str)


@dataclass
class ImpactoEscrita:
    """ESTIMATIVA ESTATICA do custo de manter um indice (nao e medicao; a medicao de INSERT/UPDATE/DELETE e `escrita.py`)."""
    tabela: str
    indices_antes: int | None
    indices_depois: int | None
    largura_chave_bytes: int | None
    largura_linha_bytes: int | None      # linha da folha: cabecalho + chave + incluidas + chave do clusterizado + overhead
    paginas_estimadas: int | None
    mb_estimados: float | None
    colunas_chave_atualizaveis: int | None   # colunas da chave/INCLUDE que nao sao identidade nem chave primaria
    premissas: list[str] = field(default_factory=list)
    nao_determinado: list[str] = field(default_factory=list)
    rotulo: str = "ESTIMATIVA ESTATICA, nao medida"


# ----------------------------------------------------------------------------------------------- seletividade baixa x custo estimado
@dataclass
class DecisaoSeletividade:
    baixa: bool                          # True: nao indexar (indice inutil ou pior que a varredura)
    criterio: str
    leituras_antes: float | None         # leituras da varredura (plano real ou paginas do esquema)
    leituras_depois: float | None        # ESTIMATIVA das leituras com o indice
    cobrindo: bool | None


def avaliar_seletividade(sel: float | None, cardinalidade: float | None, leituras_varredura: float | None, cobrindo: bool | None,
                         paginas_indice: float | None, lim: Limiares) -> DecisaoSeletividade:
    """Decide se a seletividade do predicado e baixa demais para indexar.

    Regra literal (limiar): sel > lim.sel_baixa. Regra com custo (padrao): alem do limiar, um indice NAO cobrindo so vale se
    linhas x lookup_paginas_por_linha < leituras da varredura; um indice que COBRE vale se sel x paginas do indice < leituras
    da varredura (ele le uma estrutura mais estreita que a tabela, mesmo com seletividade baixa). Todo numero e estimativa."""
    if sel is None:
        return DecisaoSeletividade(False, "seletividade desconhecida: a regra de baixa seletividade não pôde ser aplicada", leituras_varredura, None, cobrindo)
    literal = sel > lim.sel_baixa
    texto_lit = f"filtro devolve {_pct(sel)} da tabela (limiar {_pct(lim.sel_baixa)})"
    if lim.regra_literal_seletividade:
        return DecisaoSeletividade(literal, "regra literal: " + texto_lit, leituras_varredura, None, cobrindo)
    if cobrindo is True:
        if paginas_indice is None or leituras_varredura is None:
            return DecisaoSeletividade(literal, "sem tamanho do índice ou da varredura para estimar o custo; vale o limiar: " + texto_lit, leituras_varredura, None, cobrindo)
        depois = sel * paginas_indice + lim.descida_indice_paginas
        return DecisaoSeletividade(depois >= leituras_varredura, f"índice cobrindo: lê ~{_n(round(depois))} páginas (fração x tamanho do índice) contra {_n(leituras_varredura)} da varredura; {texto_lit}",
                                   leituras_varredura, depois, cobrindo)
    if cardinalidade is not None and leituras_varredura is not None:
        depois = sel * cardinalidade * lim.lookup_paginas_por_linha + lim.descida_indice_paginas
        if depois >= leituras_varredura:
            return DecisaoSeletividade(True, f"índice não cobrindo: ~{_n(round(depois))} páginas em Key Lookups ({_n(round(sel * cardinalidade))} linhas x {lim.lookup_paginas_por_linha:g}) "
                                       f"contra {_n(leituras_varredura)} da varredura; {texto_lit}", leituras_varredura, depois, cobrindo)
        return DecisaoSeletividade(literal, ("índice não cobrindo: " if not literal else "") + texto_lit + f"; Key Lookups estimados em ~{_n(round(depois))} páginas contra {_n(leituras_varredura)} da varredura",
                                   leituras_varredura, depois, cobrindo)
    return DecisaoSeletividade(literal, "sem leituras da varredura para estimar o custo; vale o limiar: " + texto_lit, leituras_varredura, None, cobrindo)


# ----------------------------------------------------------------------------------------------- impacto de escrita (estatico)
def _largura_coluna(col, lim: Limiares, comprimento_medio: float | None = None) -> tuple[int, bool, bool]:
    """(bytes, variavel, estimado). col: ColunaEsq. Tipo variavel usa o comprimento medio medido ou metade do declarado."""
    variavel = col.tipo.lower() in _TIPOS_VARIAVEIS
    if not variavel:
        return max(col.bytes, 0), False, False
    if col.bytes == -1:
        return lim.ponteiro_lob_bytes, True, True
    if comprimento_medio is not None:
        return ceil(comprimento_medio), True, False
    return ceil(col.bytes * lim.fracao_media_variavel), True, True


def estimar_impacto_escrita(
    esquema: EsquemaBanco | None, tabela: str, chave: list[str], incluidas: list[str] | None = None, limiares: Limiares | None = None,
) -> ImpactoEscrita:
    """Impacto ESTATICO de criar o indice (chave, incluidas) em `tabela`. Sem esquema, so o que nao depende dele."""
    lim = limiares or LIMIARES_PADRAO
    incluidas = list(incluidas or [])
    res = ImpactoEscrita(tabela, None, None, None, None, None, None, None)
    res.premissas.append("modelo: linha da folha = cabecalho 4 B + colunas + chave do clusterizado + (overhead de colunas variaveis) + 2 B de slot; "
                         "paginas = linhas / floor(8096 / linha) x 1,003 (niveis superiores)")
    res.nao_determinado.append("custo real de INSERT/UPDATE/DELETE (depende da carga: frequencia de escrita da tabela e das colunas atualizadas); "
                               "medir com tcclab.escrita.medir_custo_escrita")
    if esquema is None or esquema.buscar_tabela(tabela) is None:
        res.nao_determinado.append("tipos, linhas e indices da tabela (esquema nao informado ou tabela ausente do esquema)")
        return res
    t = esquema.buscar_tabela(tabela)
    existentes = esquema.indices_da(tabela)
    res.indices_antes, res.indices_depois = len(existentes), len(existentes) + 1
    clust = esquema.indice_clusterizado(tabela)
    extra = [c for c in (clust.chave if clust else ()) if c.lower() not in {x.lower() for x in chave + incluidas}]
    todas = list(chave) + incluidas + extra
    larguras: dict[str, tuple[int, bool, bool]] = {}
    for nome in todas:
        col = esquema.coluna(tabela, nome)
        if col is None:
            res.nao_determinado.append(f"coluna {nome} nao esta no esquema: largura nao estimada")
            return res
        d = esquema.densidade(tabela, nome)
        larguras[nome] = _largura_coluna(col, lim, d.comprimento_medio if d else None)
    res.largura_chave_bytes = sum(larguras[c][0] for c in chave)
    fixos = sum(w for w, v, _ in larguras.values() if not v)
    variaveis = [w for w, v, _ in larguras.values() if v]
    linha = lim.cabecalho_linha_bytes + fixos + lim.slot_bytes
    if variaveis:
        linha += (2 + ceil(len(todas) / 8)) + 2 + 2 * len(variaveis) + sum(variaveis)
    if clust is None:
        linha += 8
        res.premissas.append("tabela sem indice clusterizado (heap): acrescentado RID de 8 B por linha")
    elif not clust.unico:
        linha += 4
        res.premissas.append("clusterizado nao unico: acrescentado uniquificador de 4 B")
    res.largura_linha_bytes = linha
    if any(e for _, _, e in larguras.values()):
        res.premissas.append("coluna de tamanho variavel sem comprimento medio no esquema: assumida metade do declarado (ponteiro de 24 B para tipos max); "
                             "o erro pode chegar a 2x ou mais para essas colunas")
    por_pagina = max(1, lim.pagina_util_bytes // linha)
    folhas = ceil(t.linhas / por_pagina) if t.linhas else 0
    res.paginas_estimadas = ceil(folhas * lim.fator_niveis_superiores)
    res.mb_estimados = round(res.paginas_estimadas * 8 / 1024, 1)
    pk = {c.lower() for k in esquema.chaves_unicas(tabela) for c in k} if clust and clust.pk else set()
    atualizaveis = 0
    for nome in list(chave) + incluidas:
        col = esquema.coluna(tabela, nome)
        if col and not col.identidade and nome.lower() not in pk:
            atualizaveis += 1
    res.colunas_chave_atualizaveis = atualizaveis
    if not t.linhas:
        res.nao_determinado.append("tabela sem linhas no esquema: tamanho estimado em zero")
    return res


# ----------------------------------------------------------------------------------------------- SQL: predicados e fatos
@dataclass
class _Rascunho:
    tipo: str
    coluna: object                       # exp.Column
    operador: str | None
    funcao: str | None = None
    outra: object | None = None          # exp.Column do outro lado da juncao
    operando: object | None = None       # lado da comparacao que contem a funcao (tipo funcao)


@dataclass
class _FatosAnalise:
    predicados: list[Predicado] = field(default_factory=list)
    colunas_lidas: dict[str, dict[str, str]] = field(default_factory=dict)    # tabela_lower -> {coluna_lower: coluna}
    nomes: dict[str, tuple[str, str | None]] = field(default_factory=dict)    # tabela_lower -> (nome, esquema)
    estrela: dict[str, bool] = field(default_factory=dict)
    informadas: dict[str, list[str]] = field(default_factory=dict)            # tabela_lower -> colunas que o chamador diz precisar (SELECT *)
    correlacionadas: dict[str, int] = field(default_factory=dict)             # tabela_lower -> n de subconsultas escalares correlacionadas
    avisos: list[str] = field(default_factory=list)
    nao_resolvidas: list[str] = field(default_factory=list)


def _conjuntos(no):
    """Conjuntos de um predicado: desfaz AND e parenteses; OR e NOT ficam inteiros."""
    while isinstance(no, exp.Paren):
        no = no.this
    if isinstance(no, exp.And):
        yield from _conjuntos(no.this)
        yield from _conjuntos(no.expression)
    else:
        yield no


def _eh_coluna(x) -> bool:
    while isinstance(x, exp.Paren):
        x = x.this
    return isinstance(x, exp.Column) and not isinstance(x.this, exp.Star)


def _sem_paren(x):
    while isinstance(x, exp.Paren):
        x = x.this
    return x


_OPERADORES = {exp.EQ: "=", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}


def _rascunhos(conj) -> list[_Rascunho]:
    """Classifica um conjunto do WHERE/ON em rascunhos de predicado (um por coluna envolvida)."""
    if isinstance(conj, tuple(_OPERADORES)):
        op = _OPERADORES[type(conj)]
        esq, dir_ = conj.this, conj.expression
        cesq, cdir = _colunas_do_operando(esq), _colunas_do_operando(dir_)
        if cesq and cdir:
            if _eh_coluna(esq) and _eh_coluna(dir_) and op == "=":
                a, b = _sem_paren(esq), _sem_paren(dir_)
                return [_Rascunho("juncao", a, "=", outra=b), _Rascunho("juncao", b, "=", outra=a)]
            return [_Rascunho("outro", c, op) for c in cesq + cdir]
        lado, const = (esq, dir_) if cesq else (dir_, esq)
        cols = cesq or cdir
        if _eh_coluna(lado):
            return [_Rascunho("igualdade" if op == "=" else "intervalo", _sem_paren(lado), op)]
        if _aplica_funcao(lado):
            return [_Rascunho("funcao", c, op, operando=_sem_paren(lado)) for c in cols]
        return [_Rascunho("outro", c, op) for c in cols]
    if isinstance(conj, exp.Between):
        lado = conj.this
        cols = _colunas_do_operando(lado)
        if not cols or _colunas_do_operando(conj.args.get("low")) or _colunas_do_operando(conj.args.get("high")):
            return [_Rascunho("outro", c, "BETWEEN") for c in _colunas_do_operando(conj)]
        if _eh_coluna(lado):
            return [_Rascunho("intervalo", _sem_paren(lado), "BETWEEN")]
        if _aplica_funcao(lado):
            return [_Rascunho("funcao", c, "BETWEEN", operando=_sem_paren(lado)) for c in cols]
        return [_Rascunho("outro", c, "BETWEEN") for c in cols]
    if isinstance(conj, exp.In):
        lado = conj.this
        cols = _colunas_do_operando(lado)
        itens = conj.args.get("expressions") or []
        if conj.args.get("query") is not None or any(_colunas_do_operando(i) for i in itens):
            return [_Rascunho("outro", c, "IN") for c in cols]
        if _eh_coluna(lado):
            return [_Rascunho("igualdade", _sem_paren(lado), "IN")]
        if _aplica_funcao(lado):
            return [_Rascunho("funcao", c, "IN", operando=_sem_paren(lado)) for c in cols]
        return [_Rascunho("outro", c, "IN") for c in cols]
    if isinstance(conj, exp.Like):
        lado, padrao = conj.this, conj.expression
        cols = _colunas_do_operando(lado)
        if _colunas_do_operando(padrao) or not cols:
            return [_Rascunho("outro", c, "LIKE") for c in _colunas_do_operando(conj)]
        prefixo = isinstance(padrao, exp.Literal) and padrao.is_string and padrao.this[:1] not in ("%", "_", "[")
        if _eh_coluna(lado):
            return [_Rascunho("intervalo" if prefixo else "outro", _sem_paren(lado), "LIKE")]
        return [_Rascunho("funcao", c, "LIKE", operando=_sem_paren(lado)) for c in cols]
    if isinstance(conj, exp.Exists):
        return []
    return [_Rascunho("outro", c, type(conj).__name__.upper()) for c in _colunas_do_operando(conj)]


def _resolver(escopo, col, esquema: EsquemaBanco | None) -> tuple[exp.Table | None, str | None]:
    """(Table, motivo). Procura a fonte da coluna no escopo e nos escopos externos (colunas correlacionadas)."""
    t, motivo, _ = _resolver_externa(escopo, col, esquema)
    return t, motivo


def _resolver_externa(escopo, col, esquema: EsquemaBanco | None) -> tuple[exp.Table | None, str | None, bool]:
    """Como `_resolver`, mais a indicacao de que a coluna veio de um escopo externo (correlacao)."""
    alias = (col.table or "").lower()
    e, externa = escopo, False
    while e is not None:
        fontes = e.sources
        if alias:
            for chave, fonte in fontes.items():
                if chave.lower() == alias:
                    return (fonte, None, externa) if isinstance(fonte, exp.Table) else (None, "fonte derivada ou CTE", externa)
        else:
            tabelas = [f for f in fontes.values() if isinstance(f, exp.Table)]
            if len(fontes) == 1 and len(tabelas) == 1:
                return tabelas[0], None, externa
            if esquema is not None:
                cand = [t for t in tabelas if esquema.coluna(t.name, col.name) is not None]
                if len(cand) == 1:
                    return cand[0], None, externa
                if len(cand) > 1:
                    return None, "coluna sem qualificador presente em mais de uma tabela", externa
            elif len(fontes) > 1 or e.parent is None:
                return None, "coluna sem qualificador em consulta com mais de uma fonte, sem esquema", externa
        e, externa = e.parent, True
    return None, "tabela da coluna nao encontrada", externa


def _registrar_tabela(fatos: _FatosAnalise, t) -> str:
    k = t.name.lower()
    fatos.nomes.setdefault(k, (t.name, t.db or None))
    return k


def _extrair_sql(arvore, fonte: "_Fonte", esquema: EsquemaBanco | None, fatos: _FatosAnalise) -> None:
    raiz = build_scope(arvore)
    if raiz is None:
        fatos.avisos.append("o SQL nao tem escopo de consulta (SELECT) analisavel")
        return
    contador = 0
    for ind, escopo in enumerate(raiz.traverse()):
        sel = escopo.expression
        if not isinstance(sel, exp.Select):
            continue
        # tabelas e colunas lidas
        for col in escopo.columns:
            if isinstance(col.this, exp.Star):
                continue
            t, _ = _resolver(escopo, col, esquema)
            if t is not None:
                k = _registrar_tabela(fatos, t)
                fatos.colunas_lidas.setdefault(k, {}).setdefault(col.name.lower(), col.name)
        for e in sel.expressions:
            est = e if isinstance(e, exp.Star) else (e if isinstance(e, exp.Column) and isinstance(e.this, exp.Star) else None)
            if est is None:
                continue
            tabelas = [f for f in escopo.sources.values() if isinstance(f, exp.Table)]
            if isinstance(est, exp.Column) and est.table:
                tabelas = [f for k2, f in escopo.sources.items() if k2.lower() == est.table.lower() and isinstance(f, exp.Table)]
            for t in tabelas:
                fatos.estrela[_registrar_tabela(fatos, t)] = True
        # subconsulta escalar correlacionada
        if escopo.is_subquery:
            pai = sel.parent
            semi = isinstance(pai, exp.Exists) or (isinstance(pai, exp.Subquery) and isinstance(pai.parent, (exp.In, exp.Any, exp.All, exp.Lateral)))
            if not semi and [c for c in escopo.external_columns if c.table]:
                for t in {f.name.lower(): f for f in escopo.sources.values() if isinstance(f, exp.Table)}.values():
                    k = _registrar_tabela(fatos, t)
                    fatos.correlacionadas[k] = fatos.correlacionadas.get(k, 0) + 1
        # predicados
        origens = []
        if sel.args.get("where") is not None:
            origens.append(("subconsulta (correlação)" if escopo.is_subquery else "WHERE", sel.args["where"].this))
        for j in sel.args.get("joins") or []:
            if j.args.get("on") is not None:
                origens.append(("JOIN ... ON", j.args["on"]))
        for clausula, raiz_pred in origens:
            for conj in _conjuntos(raiz_pred):
                trecho = fonte.trecho(conj)
                for r in _rascunhos(conj):
                    if r.tipo == "funcao" and r.funcao is None:
                        r.funcao = "/".join(dict.fromkeys(_nomes_de_funcao(trecho[0] if trecho else None, r.operando))).upper()
                    t, motivo, externa = _resolver_externa(escopo, r.coluna, esquema)
                    if t is None:
                        fatos.nao_resolvidas.append(f"{r.coluna.sql(dialect='tsql')}: {motivo}")
                        continue
                    if externa:
                        continue   # lado externo de uma correlacao: o predicado fica registrado no lado interno
                    k = _registrar_tabela(fatos, t)
                    outra = None
                    if r.outra is not None:
                        ot, _ = _resolver(escopo, r.outra, esquema)
                        outra = (f"{ot.name}." if ot is not None else "") + r.outra.name
                        if ot is not None and ot.name.lower() == k and (r.outra.table or "").lower() == (r.coluna.table or "").lower():
                            r = _Rascunho("outro", r.coluna, "=")   # a.x = a.y: mesma tabela, nao e juncao
                            outra = None
                    contador += 1
                    fatos.predicados.append(Predicado(
                        id=f"PR{contador}", tabela=t.name, esquema_tabela=t.db or None, coluna=r.coluna.name, tipo=r.tipo,
                        operador=r.operador, clausula=clausula, trecho_sql=trecho[0] if trecho else _curto(conj.sql(dialect="tsql")),
                        linha_sql=trecho[1] if trecho else None, funcao=r.funcao, outra_coluna=outra, escopo=ind,
                    ))


# ----------------------------------------------------------------------------------------------- plano
@dataclass
class _Plano:
    nos: list = field(default_factory=list)          # lista de _No (diagnostico)
    tipo: str = "nenhum"
    avisos: list[str] = field(default_factory=list)
    motivo: str | None = None
    indices_ausentes: list[dict] = field(default_factory=list)
    real: bool = False


def _ler_plano(xml: str | None) -> _Plano:
    if not xml or not xml.strip():
        return _Plano(motivo="sem plano de execução fornecido")
    try:
        pa = analisar_plano(xml)
        raiz = ET.fromstring(xml)
    except ET.ParseError as e:
        return _Plano(tipo="invalido", motivo=f"XML inválido: {_curto(str(e), 160)}")
    ins = pa.principal()
    if ins is None or not ins.operadores:
        return _Plano(tipo="invalido", motivo="plano sem operadores")
    stmt = list(raiz.iter(NS + "StmtSimple"))[pa.instrucoes.index(ins)]
    qp = stmt.find(NS + "QueryPlan")
    rel = qp.find(NS + "RelOp") if qp is not None else None
    if rel is None:
        return _Plano(tipo="invalido", motivo="plano sem RelOp raiz")
    p = _Plano(real=ins.eh_plano_real, tipo="real" if ins.eh_plano_real else "estimado", indices_ausentes=list(ins.indices_ausentes))
    if sum(1 for i in pa.instrucoes if i.operadores) > 1:
        p.avisos.append("o plano tem mais de uma instrução com operadores; só a primeira foi analisada")
    p.nos = list(_percorrer(_construir(rel, {o.node_id: o for o in ins.operadores})))
    return p


def _colunas_seek(no) -> list[str]:
    """Colunas do indice usadas em busca (RangeColumns de SeekPredicates), na ordem do plano."""
    cols = []
    for f in no.elem:
        for sub in f:
            if _tag(sub) in ("SeekPredicates", "SeekPredicate", "SeekPredicateNew"):
                for rc in sub.iter(NS + "RangeColumns"):
                    cols += [cr.get("Column") for cr in rc.iter(NS + "ColumnReference") if cr.get("Column")]
    return [c.replace("[", "").replace("]", "") for c in cols]


def _colunas_do_no(no) -> set[str]:
    cols = set(c.lower() for c in _colunas_seek(no))
    if no.predicado is not None:
        cols |= _colunas_do_predicado(no.predicado)
    return cols


def _acessos(plano: _Plano, tabela: str) -> list:
    return [n for n in plano.nos if n.simples and n.simples.lower() == tabela.lower() and not n.op.lookup and n.op.physical_op != "RID Lookup"]


def _lookups(plano: _Plano, tabela: str) -> list:
    return [n for n in plano.nos if n.simples and n.simples.lower() == tabela.lower() and n.op.lookup]


def _fracoes(no, card_esquema: float | None) -> tuple[float | None, float | None, float | None]:
    """(sel_estimada, sel_real, cardinalidade). Linhas por execucao (Seek dentro de Nested Loops roda varias vezes)."""
    card = no.op.table_cardinality or card_esquema
    if not card:
        return None, None, None
    est = min(1.0, no.op.est_rows / card) if no.op.est_rows is not None else None
    real = None
    if no.op.tem_contadores_reais and no.op.actual_executions > 0:
        real = min(1.0, no.op.actual_rows / no.op.actual_executions / card)
    return est, real, float(card)


# ----------------------------------------------------------------------------------------------- avaliacao por tabela
def _projecao(nome: str, fatos: _FatosAnalise, esquema: EsquemaBanco | None, chave: list[str], lim: Limiares) -> tuple[bool | None, str]:
    """A projecao da consulta nesta tabela cabe em INCLUDE? (True/False/None) e o porque."""
    k = nome.lower()
    if fatos.estrela.get(k) and k not in fatos.informadas:
        return False, "SELECT * (todas as colunas)"
    lidas = fatos.colunas_lidas.get(k, {})
    if esquema is None or esquema.buscar_tabela(nome) is None:
        return None, "sem esquema: tipos das colunas desconhecidos"
    clust = esquema.indice_clusterizado(nome)
    ja = {c.lower() for c in chave} | {c.lower() for c in (clust.chave if clust else ())}
    extras = [c for cl, c in lidas.items() if cl not in ja]
    for c in extras:
        col = esquema.coluna(nome, c)
        if col is None:
            return None, f"coluna {c} fora do esquema"
        if col.bytes == -1:
            return False, f"coluna {c} é de tipo max"
    if len(extras) > lim.include_max_colunas:
        return False, f"{len(extras)} colunas além da chave (máximo {lim.include_max_colunas})"
    return True, f"{len(extras)} coluna(s) além da chave"


def _prefixo_utilizavel(chave: tuple[str, ...], E: set[str], R: set[str]) -> int:
    """Colunas iniciais da chave que o filtro consegue usar em busca: igualdades seguidas de no maximo uma de intervalo."""
    i = 0
    while i < len(chave) and chave[i].lower() in E:
        i += 1
    if i < len(chave) and chave[i].lower() in R:
        i += 1
    return i


def _indices_da_tabela(nome: str, esquema: EsquemaBanco | None, acessos: list, lookups: list) -> list[IndiceVisto]:
    vistos: list[IndiceVisto] = []
    if esquema is not None:
        for i in esquema.indices_da(nome):
            vistos.append(IndiceVisto(i.nome, i.tipo.upper(), i.chave, i.incluidas, "esquema", True))
    nomes = {v.nome.lower() for v in vistos}
    for n in acessos + lookups:
        if n.indice and n.indice.lower() not in nomes:
            nomes.add(n.indice.lower())
            tipo = (n.op.tipo_indice or "").upper() or "DESCONHECIDO"
            vistos.append(IndiceVisto(n.indice, tipo, tuple(_colunas_seek(n)), (), "plano", False))
    return vistos


class _Avaliador:
    def __init__(self, fatos: _FatosAnalise, plano: _Plano, esquema: EsquemaBanco | None, lim: Limiares):
        self.fatos, self.plano, self.esquema, self.lim = fatos, plano, esquema, lim

    # ---- vinculo predicado x plano
    def ligar_plano(self, p: Predicado, preds_tabela: list[Predicado]) -> None:
        card_esq = float(self.esquema.linhas(p.tabela)) if self.esquema and self.esquema.linhas(p.tabela) else None
        if self.esquema is not None:
            p.sel_coluna = self.esquema.seletividade_coluna(p.tabela, p.coluna)
            p.indices_que_cobrem = [i.nome for i in self.esquema.indices_da(p.tabela) if i.chave and i.chave[0].lower() == p.coluna.lower()]
        if p.sel_coluna is None:
            p.nao_determinado.append("seletividade da coluna (distintos/total): sem densidade de estatística no esquema")
        if self.plano.tipo not in ("real", "estimado"):
            p.nao_determinado.append("seletividade do predicado: sem plano de execução")
            return
        mencionam = [n for n in _acessos(self.plano, p.tabela) if p.coluna.lower() in _colunas_do_no(n)]
        if not mencionam:
            p.nao_determinado.append("nenhum operador de acesso do plano cita esta coluna (o filtro pode ter sido avaliado em outro operador): seletividade não determinada")
            return
        n = mencionam[0]
        p.no_plano, p.operador_plano = n.op.node_id, n.nome
        p.sel_estimada, p.sel_real, p.cardinalidade = _fracoes(n, card_esq)
        outras = [q for q in preds_tabela if q.tipo != "outro" and q.escopo == p.escopo and q.coluna.lower() in _colunas_do_no(n)]
        p.conjunta = len(outras) > 1   # varios predicados da mesma consulta/escopo no mesmo operador (ex.: YEAR e MONTH da mesma coluna)
        base = "plano real" if self.plano.real else "plano estimado"
        if p.cardinalidade is None:
            p.nao_determinado.append("cardinalidade da tabela ausente do plano e do esquema")
        else:
            saida_est = n.op.est_rows
            texto = f"Nó {n.op.node_id} ({n.nome}) em {n.tabela} [{base}]: estima {_n(saida_est)} de {_n(p.cardinalidade)} linhas"
            if p.sel_estimada is not None:
                texto += f" ({_pct(p.sel_estimada)})"
            if p.sel_real is not None:
                texto += f"; real: {_n(n.op.actual_rows / max(1, n.op.actual_executions))} por execução ({_pct(p.sel_real)})"
            if n.op.predicado:
                texto += f"; predicado do operador: {_curto(n.op.predicado, 110)}"
            p.evidencia.append(texto)
        if p.conjunta:
            p.nao_determinado.append("o operador avalia mais de um predicado: a fração é da conjunção, não deste predicado isolado")
        if p.tipo == "juncao" and "PROBE" in (n.op.predicado or "").upper():
            p.evidencia.append("fração medida depois do filtro de bitmap (PROBE) da junção hash: é a parte da tabela que a junção de fato consome")
        if p.sel_real is None and self.plano.real:
            p.nao_determinado.append("operador sem execução registrada no plano real")
        if p.sel_real is None and p.tipo == "funcao":
            p.nao_determinado.append("predicado com função: a estimativa do plano para a fração pode divergir muito do real (ver doc 26, seção 2.2)")

    # ---- causas por tabela
    def avaliar(self, nome: str, preds: list[Predicado]) -> TabelaAnalisada:
        fatos, plano, esq, lim = self.fatos, self.plano, self.esquema, self.lim
        k = nome.lower()
        nome_ok, sch = fatos.nomes[k]
        tinfo = esq.buscar_tabela(nome_ok) if esq else None
        acessos, lookups = _acessos(plano, nome_ok), _lookups(plano, nome_ok)
        indices = _indices_da_tabela(nome_ok, esq, acessos, lookups) if (esq or plano.tipo in ("real", "estimado")) else []
        sarg = [p for p in preds if p.tipo in ("igualdade", "intervalo", "juncao")]
        chave_ordem = [p.coluna for p in sarg + [q for q in preds if q.tipo == "funcao"]]
        estreita, motivo_proj = _projecao(nome_ok, fatos, esq, list(dict.fromkeys(chave_ordem)), lim)
        lidas = list(fatos.colunas_lidas.get(k, {}).values())
        t = TabelaAnalisada(
            tabela=nome_ok, esquema_tabela=sch, linhas=tinfo.linhas if tinfo else None, paginas=tinfo.paginas if tinfo else None,
            indices=indices, colunas_lidas=lidas, select_estrela=bool(fatos.estrela.get(k)), projecao_estreita=estreita,
            colunas_informadas=k in fatos.informadas,
            predicados=[p.id for p in preds], causas=[], subconsultas_correlacionadas=fatos.correlacionadas.get(k, 0),
            nos_plano=[n.op.node_id for n in acessos],
        )
        t.notas.append(f"projeção: {motivo_proj}")
        if esq is None:
            t.notas.append("sem esquema: índices existentes conhecidos só pelo plano e tipos das colunas desconhecidos")
        causas: list[Causa] = []
        # 1. subconsulta correlacionada repetida
        if t.subconsultas_correlacionadas >= 2:
            nl = [n for n in plano.nos if n.correlacionado and any(s.simples and s.simples.lower() == k for s in _percorrer(n))]
            ev = [f"{t.subconsultas_correlacionadas} subconsultas escalares correlacionadas sobre {nome_ok} no SQL"]
            nd = []
            if plano.tipo in ("real", "estimado"):
                if nl:
                    ev.append("o plano mostra Nested Loops correlacionado (nó " + ", ".join(str(n.op.node_id) for n in nl) + ") sobre esta tabela")
                else:
                    ev.append("o plano não mostra Nested Loops correlacionado sobre esta tabela: o otimizador já decorrelacionou (Hash Match), "
                              "então o ganho da reescrita aparece como menos leituras da tabela, não como menos execuções do lado interno")
            else:
                nd.append("sem plano: não se sabe se o otimizador já decorrelacionou as subconsultas")
            causas.append(Causa("subconsulta_correlacionada_repetida", nome_ok, tuple(dict.fromkeys(p.coluna for p in preds if p.tipo == "juncao")),
                                "reescrever_subconsulta", ev, nd, {"subconsultas": t.subconsultas_correlacionadas, "plano_com_nested_loops": bool(nl)}))
        elif t.subconsultas_correlacionadas == 1:
            t.notas.append("uma subconsulta escalar correlacionada sobre esta tabela: o otimizador pode já tê-la convertido em junção (caso S5 do catálogo)")
        # 2. predicado nao sargavel
        func = [p for p in preds if p.tipo == "funcao"]
        if func:
            ev = [f"{p.funcao} sobre {p.tabela}.{p.coluna} em {p.clausula}" + (f" (linha {p.linha_sql})" if p.linha_sql else "") + f": {p.trecho_sql}" for p in func]
            ev += [e for p in func for e in p.evidencia[:1]]
            ev = list(dict.fromkeys(ev))
            nd = []
            if plano.tipo not in ("real", "estimado"):
                nd.append("sem plano: não se sabe se o otimizador simplifica a expressão nem quantas linhas o filtro devolve")
            causas.append(Causa("predicado_nao_sargavel", nome_ok, tuple(dict.fromkeys(p.coluna for p in func)), "reescrever_predicado", ev, nd,
                                {"funcoes": sorted({p.funcao or "?" for p in func})}))
        # 3. indices: por acesso do plano (ou um unico acesso logico sem plano)
        if tinfo is not None and tinfo.linhas < lim.tabela_minima_linhas and (sarg or func):
            causas.append(Causa("sem_problema", nome_ok, (), "nenhuma",
                                [f"tabela pequena: {_n(tinfo.linhas)} linhas (limiar {_n(lim.tabela_minima_linhas)}); índice não se justifica"]))
        else:
            causas += self._causas_de_indice(t, nome_ok, preds, acessos, lookups, indices, estreita, motivo_proj)
        # funde causas iguais (mesmo codigo e colunas) vindas de acessos diferentes
        fundidas: dict[tuple, Causa] = {}
        for c in causas:
            chave = (c.codigo, c.colunas)
            if chave in fundidas:
                alvo = fundidas[chave]
                alvo.evidencia += [e for e in c.evidencia if e not in alvo.evidencia]
                alvo.nao_determinado += [e for e in c.nao_determinado if e not in alvo.nao_determinado]
                alvo.detalhes["nos"] = sorted(set(alvo.detalhes.get("nos", [])) | set(c.detalhes.get("nos", [])))
            else:
                fundidas[chave] = c
        t.causas = list(fundidas.values()) or [Causa("sem_problema", nome_ok, (), "nenhuma", ["nenhum predicado indexável na tabela"])]
        return t

    def _causas_de_indice(self, t: TabelaAnalisada, nome: str, preds: list[Predicado], acessos: list, lookups: list,
                          indices: list[IndiceVisto], estreita: bool | None, motivo_proj: str) -> list[Causa]:
        lim, plano = self.lim, self.plano
        causas: list[Causa] = []
        tem_plano = plano.tipo in ("real", "estimado")
        grupos: list[tuple] = []
        if tem_plano:
            usados = set()
            for n in acessos:
                ps = [p for p in preds if p.tipo != "outro" and p.no_plano == n.op.node_id]
                if ps:
                    grupos.append((n, ps))
                    usados.add(n.op.node_id)
            sem_no = [p for p in preds if p.tipo in ("igualdade", "intervalo", "funcao", "juncao") and p.no_plano is None]
            if sem_no and not grupos:
                grupos.append((None, sem_no))
        else:
            ps = [p for p in preds if p.tipo in ("igualdade", "intervalo", "funcao", "juncao")]
            if ps:
                grupos.append((None, ps))
        for n, ps in grupos:
            # juncao ja atendida por indice existente (ex.: PK) nao deve mascarar um filtro sem indice na mesma tabela
            servidas = {p.coluna.lower() for p in ps if p.tipo == "juncao" and any(i.chave and i.chave[0].lower() == p.coluna.lower() for i in indices)}
            if servidas and any(p.tipo in ("igualdade", "intervalo", "funcao") for p in ps):
                ps = [p for p in ps if not (p.tipo == "juncao" and p.coluna.lower() in servidas)]
            E = [p.coluna for p in ps if p.tipo in ("igualdade", "juncao")]
            Rr = [p.coluna for p in ps if p.tipo == "intervalo"]
            F = [p.coluna for p in ps if p.tipo == "funcao"]
            Eset, Rset = {c.lower() for c in E}, {c.lower() for c in Rr}
            alvo = list(dict.fromkeys(E + Rr))
            alvo_hip = list(dict.fromkeys(E + Rr + F))
            sel_est = max((p.sel_estimada for p in ps if p.sel_estimada is not None), default=None)
            sel_real = max((p.sel_real for p in ps if p.sel_real is not None), default=None)
            sel = sel_real if sel_real is not None else sel_est
            base = "real" if sel_real is not None else ("estimada" if sel_est is not None else None)
            nos_ids = [n.op.node_id] if n is not None else []
            ev_no = ps[0].evidencia[:1] if ps and ps[0].evidencia else []
            nd: list[str] = []
            if not tem_plano:
                nd.append("sem plano: não se sabe se a tabela é varrida nem quantas linhas o filtro devolve")
            if t.indices and any(not i.completo for i in t.indices):
                nd.append("índices vistos só no plano: chave conhecida apenas em parte")
            if self.esquema is None:
                nd.append("sem esquema: outros índices da tabela podem existir e não foram considerados")
            if sel is not None and sel_real is None:
                nd.append("seletividade só estimada pelo plano (sem contadores reais)")
            # existe indice utilizavel (1a coluna-chave e coluna de predicado sargavel)?
            usaveis = [i for i in indices if i.chave and i.chave[0].lower() in {c.lower() for c in alvo}]
            lk = [x for x in lookups if (_execucoes(x, plano.real) or 0) >= lim.lookup_execucoes_minimas]
            if usaveis and lk:
                idx = usaveis[0]
                ex = sum(x.op.actual_executions for x in lk) if plano.real else None
                lr = sum(x.op.actual_logical_reads for x in lk) if plano.real else None
                ev = ev_no + [f"índice {idx.nome} é usado na busca, mas o plano volta à tabela com Key Lookup (nó " + ", ".join(str(x.op.node_id) for x in lk) + ")"
                              + (f": {_n(ex)} execuções, {_n(lr)} leituras lógicas" if ex is not None else " [estimado]")]
                causas.append(Causa("indice_nao_cobre", nome, tuple(idx.chave), "cobrir_indice_ou_reduzir_projecao", ev, nd,
                                    {"indice": idx.nome, "nos": nos_ids, "lookups": ex, "projecao": motivo_proj}))
                continue
            # ordem das colunas: indice com colunas do filtro em ordem que impede a busca
            pior = None
            for i in indices:
                if not i.completo:
                    continue
                P = [c for c in i.chave if c.lower() in Eset | Rset]
                if P and _prefixo_utilizavel(i.chave, Eset, Rset) < len(P):
                    pior = i
                    break
            if pior is not None and not any(_prefixo_utilizavel(i.chave, Eset, Rset) == len([c for c in i.chave if c.lower() in Eset | Rset]) > 0 for i in usaveis):
                ev = ev_no + [f"índice {pior.nome} ({', '.join(pior.chave)}) tem colunas do filtro, mas a ordem impede a busca por todas: "
                              f"igualdades {sorted(E) or '-'}, intervalo {sorted(Rr) or '-'}; prefixo utilizável: {_prefixo_utilizavel(pior.chave, Eset, Rset)} de {len([c for c in pior.chave if c.lower() in Eset | Rset])} coluna(s) do filtro"]
                causas.append(Causa("ordem_colunas_inadequada", nome, tuple(pior.chave), "ajustar_indice", ev, nd, {"indice": pior.nome, "nos": nos_ids}))
                continue
            tinfo = self.esquema.buscar_tabela(nome) if self.esquema else None
            card = ps[0].cardinalidade or (float(tinfo.linhas) if tinfo else None)
            leit_scan = (n.op.actual_logical_reads if (n is not None and plano.real and n.op.actual_logical_reads) else None) or (float(tinfo.paginas) if tinfo else None)
            pag_idx = None
            if estreita is True and self.esquema is not None:
                clust = self.esquema.indice_clusterizado(nome)
                ja = {c.lower() for c in alvo_hip} | {c.lower() for c in (clust.chave if clust else ())}
                incl_t = [c for cl, c in self.fatos.colunas_lidas.get(nome.lower(), {}).items() if cl not in ja]
                pag_idx = estimar_impacto_escrita(self.esquema, nome, alvo_hip, incl_t, lim).paginas_estimadas
            dec = avaliar_seletividade(sel, card, leit_scan, estreita, pag_idx, lim)
            baixa = dec.baixa
            if usaveis:
                varre = n is not None and n.op.physical_op in _VARREDURAS
                if varre and baixa:
                    causas.append(Causa("baixa_seletividade", nome, tuple(alvo), "nao_indexar",
                                        ev_no + [f"existe índice utilizável ({usaveis[0].nome}) mas o plano varre a tabela; {dec.criterio}"],
                                        nd, {"nos": nos_ids, "sel": sel, "base_sel": base}))
                elif varre:
                    causas.append(Causa("indeterminada", nome, tuple(alvo), "investigar",
                                        ev_no + [f"existe índice utilizável ({usaveis[0].nome}) e o filtro é seletivo ({_pct(sel) if sel is not None else '?'}), mas o plano varre a tabela"],
                                        nd + ["por que o otimizador não usou o índice (estatística, custo, tipo do parâmetro) não pôde ser determinado"],
                                        {"nos": nos_ids}))
                else:
                    causas.append(Causa("sem_problema", nome, tuple(alvo), "nenhuma",
                                        ev_no + [f"índice {usaveis[0].nome} atende o filtro" + (" (busca no plano)" if n is not None else " (plano ausente: uso não confirmado)")],
                                        nd, {"nos": nos_ids}))
                continue
            if not alvo_hip:
                continue
            hipotetico = not alvo
            if tem_plano and n is not None and n.op.physical_op not in _VARREDURAS and not hipotetico:
                causas.append(Causa("sem_problema", nome, tuple(alvo), "nenhuma", ev_no + [f"o plano acessa a tabela por {n.op.physical_op} em {n.indice or '?'}"], nd, {"nos": nos_ids}))
                continue
            ev = list(ev_no)
            if n is not None and n.op.physical_op in _VARREDURAS:
                ev.append(f"nenhum índice existente começa por {', '.join(alvo_hip)}; o plano varre a tabela ({n.op.physical_op}, nó {n.op.node_id})")
            else:
                ev.append(f"nenhum índice conhecido começa por {', '.join(alvo_hip)}")
            for mi in plano.indices_ausentes:
                if mi.get("tabela", "").replace("[", "").replace("]", "").split(".")[-1].lower() == nome.lower():
                    cols = mi["colunas"]
                    ev.append("o otimizador sugeriu (informativo, não é autoridade): chave " + ", ".join(
                        c.strip("[]") for c in cols.get("EQUALITY", []) + cols.get("INEQUALITY", [])) + f" (impacto {mi.get('impacto')}%)")
            det = {"nos": nos_ids, "sel": sel, "base_sel": base, "condicionada_a_reescrita": hipotetico, "projecao_estreita": estreita, "projecao": motivo_proj}
            if estreita is False and self.fatos.estrela.get(nome.lower()) and nome.lower() not in self.fatos.informadas:
                ev.append("a consulta usa SELECT * nesta tabela: nenhum índice de poucas colunas consegue cobri-la (Key Lookup por linha encontrada)")
            det.update({"leituras_varredura": dec.leituras_antes, "leituras_estimadas_com_indice": dec.leituras_depois, "criterio_seletividade": dec.criterio})
            nd2 = list(nd)
            if sel is None:
                nd2.append(dec.criterio)
            if estreita is None:
                nd2.append(f"se o índice cobriria a consulta (projeção estreita) não pôde ser determinado: {motivo_proj}")
            if baixa:
                ev.append(f"seletividade baixa: {dec.criterio}")
                causas.append(Causa("baixa_seletividade", nome, tuple(alvo_hip), "nao_indexar", ev, nd2, det))
            else:
                if sel is not None and sel > lim.sel_baixa:
                    ev.append(f"seletividade baixa pelo limiar ({_pct(sel)} > {_pct(lim.sel_baixa)}), mas o índice cobre a consulta e o custo estimado compensa: {dec.criterio}")
                    det["ressalva_baixa_sel"] = True
                causas.append(Causa("indice_ausente", nome, tuple(alvo_hip), "criar_indice_cobrindo" if det.get("ressalva_baixa_sel") else "criar_indice", ev, nd2, det))
        return causas


# ----------------------------------------------------------------------------------------------- API
def _aplicar_colunas_informadas(fatos: _FatosAnalise, esquema: EsquemaBanco | None, informadas, res: AnaliseIndices) -> None:
    """SELECT *: se o chamador informou as colunas de que precisa, a projecao passa a ser essa lista (mais as colunas dos
    predicados). Lista unica vale quando ha uma so tabela com *; com mais de uma, informe um dicionario tabela -> colunas."""
    if not informadas:
        return
    estrelas = [k for k, v in fatos.estrela.items() if v]
    if isinstance(informadas, dict):
        mapa = {chave_tabela(k)[1]: list(v) for k, v in informadas.items()}
    elif len(estrelas) == 1:
        mapa = {estrelas[0]: list(informadas)}
    else:
        res.avisos.append("colunas_necessarias ignoradas: há SELECT * sobre mais de uma tabela (ou nenhum); informe um dicionário tabela -> colunas")
        return
    for k, cols in mapa.items():
        if k not in fatos.estrela:
            res.avisos.append(f"colunas_necessarias para {k} ignoradas: a tabela não tem SELECT * na consulta")
            continue
        nome = fatos.nomes[k][0]
        limpas = [c.split(".")[-1].replace("[", "").replace("]", "").strip() for c in cols]
        if esquema is not None and esquema.buscar_tabela(nome) is not None:
            ruins = [c for c in limpas if esquema.coluna(nome, c) is None]
            if ruins:
                res.avisos.append(f"colunas_necessarias de {nome} ignoradas: {', '.join(ruins)} não existe(m) na tabela")
                continue
        fatos.informadas[k] = limpas
        lidas = fatos.colunas_lidas.setdefault(k, {})
        for c in limpas:
            lidas.setdefault(c.lower(), c)


def analisar_indices(
    sql: str, plano_xml: str | None = None, esquema: EsquemaBanco | None = None, limiares: Limiares | None = None,
    diagnostico: Diagnostico | None = None, colunas_necessarias: "list[str] | dict[str, list[str]] | None" = None,
) -> AnaliseIndices:
    """Analise de indices de uma consulta. Offline. Sem plano ou sem esquema o resultado diz o que deixou de ser determinado.
    `colunas_necessarias`: colunas que a aplicacao realmente usa quando a consulta tem SELECT * (so o chamador sabe)."""
    lim = limiares or LIMIARES_PADRAO
    plano = _ler_plano(plano_xml)
    res = AnaliseIndices([], [], False, None, plano.tipo, esquema is not None, lim, list(plano.avisos))
    if plano.tipo == "invalido":
        res.avisos.append(f"plano inválido ({plano.motivo}): ignorado")
    if plano.tipo == "nenhum":
        res.nao_determinado.append("plano de execução ausente: seletividade dos predicados e uso real dos índices não determinados")
    if esquema is None:
        res.nao_determinado.append("esquema ausente: índices existentes, tipos das colunas, linhas das tabelas e seletividade das colunas não determinados")
    if sqlglot is None:
        res.motivo_sql = "sqlglot não instalado (extra `diagnostico`)"
        return res
    try:
        arvores = [a for a in sqlglot.parse(sql, read="tsql") if a is not None]
        fonte = _Fonte(sql)
    except (SqlglotError, RecursionError) as e:
        res.motivo_sql = f"ParseError: {_curto(str(e), 200)}"
        return res
    selects = [a for a in arvores if a.find(exp.Select)]
    if not selects:
        res.motivo_sql = "texto não reconhecido como consulta SELECT"
        return res
    res.sql_analisavel = True
    fatos = _FatosAnalise()
    for a in selects:
        _extrair_sql(a, fonte, esquema, fatos)
    if len(selects) > 1:
        res.avisos.append(f"{len(selects)} consultas no texto; predicados de todas foram reunidos")
    _aplicar_colunas_informadas(fatos, esquema, colunas_necessarias, res)
    ava = _Avaliador(fatos, plano, esquema, lim)
    por_tabela: dict[str, list[Predicado]] = {}
    for p in fatos.predicados:
        por_tabela.setdefault(p.tabela.lower(), []).append(p)
    for p in fatos.predicados:
        ava.ligar_plano(p, por_tabela[p.tabela.lower()])
    res.predicados = fatos.predicados
    ordem = list(dict.fromkeys(list(fatos.nomes)))
    for k in ordem:
        res.tabelas.append(ava.avaliar(k, por_tabela.get(k, [])))
    if fatos.nao_resolvidas:
        res.nao_determinado.append("predicados cuja tabela não foi resolvida (ignorados): " + "; ".join(sorted(set(fatos.nao_resolvidas))))
    if any(p.tipo == "outro" for p in fatos.predicados):
        res.nao_determinado.append("predicados em OR/NOT, desigualdades, IS NULL, LIKE com curinga inicial e comparações entre colunas com expressão não são avaliados como sargáveis")
    if diagnostico is not None:
        for t in res.tabelas:
            for c in t.causas:
                for a in diagnostico.achados:
                    if t.tabela.lower() in {x.lower() for x in a.tabelas}:
                        c.evidencia.append(f"Diagnóstico ① {a.id} ({a.regra}, {a.nivel}): {_curto(a.evidencia, 120)}")
    return res


def formatar(a: AnaliseIndices) -> str:
    """Relatorio em texto para terminal."""
    out = [f"Plano: {a.plano}. SQL analisável: {'sim' if a.sql_analisavel else 'NÃO (' + str(a.motivo_sql) + ')'}. Esquema: {'sim' if a.esquema_fornecido else 'não'}."]
    out += [f"Aviso: {x}" for x in a.avisos]
    for p in a.predicados:
        sel = "?" if p.sel_predicado is None else f"{_pct(p.sel_predicado)} ({p.base_sel}{', conjunta' if p.conjunta else ''})"
        selc = "n/d" if p.sel_coluna is None else _pct(p.sel_coluna)
        out.append(f"[{p.id}] {p.tabela}.{p.coluna} {p.tipo}{' ' + p.funcao if p.funcao else ''} {p.operador or ''} | sel. predicado {sel} | sel. coluna {selc} | índices: {', '.join(p.indices_que_cobrem) or '-'}")
        out.append(f"    SQL: {p.trecho_sql}" + (f" (linha {p.linha_sql})" if p.linha_sql else ""))
        out += [f"    evidência: {e}" for e in p.evidencia]
    for t in a.tabelas:
        out.append(f"Tabela {t.tabela}: {_n(t.linhas)} linhas; índices: " + ("; ".join(f"{i.nome} ({', '.join(i.chave)})" for i in t.indices) or "nenhum conhecido"))
        for c in t.causas:
            out.append(f"  causa: {c.codigo} -> {c.recomendacao} {('[' + ', '.join(c.colunas) + ']') if c.colunas else ''}")
            out += [f"    evidência: {e}" for e in c.evidencia]
            out += [f"    NÃO determinado: {e}" for e in c.nao_determinado]
    out += [f"NÃO determinado: {x}" for x in a.nao_determinado]
    return "\n".join(out)


def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Analise de indices (camada 2). Offline: nao conecta ao SQL Server")
    p.add_argument("--sql", required=True)
    p.add_argument("--plano")
    p.add_argument("--esquema", help="JSON de schema.salvar / experimento/schema_so2010.json")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    r = analisar_indices(Path(a.sql).read_text(encoding="utf-8"), Path(a.plano).read_text(encoding="utf-8") if a.plano else None,
                         carregar(a.esquema) if a.esquema else None)
    print(r.para_json() if a.json else formatar(r))
    return 0


if __name__ == "__main__":
    sys.exit(principal())
