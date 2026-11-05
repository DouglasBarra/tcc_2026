"""Esquema do banco (metadados) para as camadas de Analise e Prescricao do framework.

`inspecionar(con)` le, por uma `Conexao`, colunas e tipos, linhas por tabela, indices (chave, incluidas, tipo) e, quando
existirem, a densidade das estatisticas; devolve um `EsquemaBanco` serializavel em JSON (`salvar`/`carregar`). So metadados:
nenhuma linha de dado de usuario e lida, nada e alterado no banco. O arquivo JSON e o formato do instantaneo
`experimento/schema_so2010.json` (colunas, tabelas, indices em linhas planas), com os campos opcionais `densidades`,
`colacao_banco` e `avisos`; um instantaneo antigo, sem esses campos, continua carregando.

Seletividade da COLUNA, no sentido do texto do projeto (knowledge/03, secao 2.2): valores distintos / total de linhas.
So existe quando ha densidade de estatistica: distintos = 1 / densidade ("All density" do vetor de densidade da primeira
coluna da estatistica). Sem densidade, `seletividade_coluna` devolve None; nunca e estimada por outro caminho.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .driver import Conexao

VERSAO_FORMATO = 1
_LIMITE_LINHAS = 1_000_000   # guardar_linhas das consultas de metadados (o driver so retem ate esse numero)


# ----------------------------------------------------------------------------------------------- estruturas
@dataclass(frozen=True)
class ColunaEsq:
    esquema: str
    tabela: str
    ordem: int
    coluna: str
    tipo: str
    bytes: int               # max_length do SQL Server: -1 para os tipos max; nvarchar(40) = 80
    anulavel: bool
    identidade: bool = False
    colacao: str | None = None   # so tipos de texto; None = nao informada


@dataclass(frozen=True)
class TabelaEsq:
    esquema: str
    tabela: str
    linhas: int
    paginas: int


@dataclass(frozen=True)
class Indice:
    esquema: str
    tabela: str
    nome: str
    tipo: str                    # CLUSTERED | NONCLUSTERED | ... (type_desc)
    unico: bool
    pk: bool
    chave: tuple[str, ...]       # colunas-chave na ordem do indice
    incluidas: tuple[str, ...] = ()

    @property
    def clusterizado(self) -> bool:
        return self.tipo.upper() == "CLUSTERED"


@dataclass(frozen=True)
class DensidadeEsq:
    """Densidade da primeira coluna de uma estatistica (all density). distintos = 1 / densidade."""
    esquema: str
    tabela: str
    estatistica: str
    coluna: str
    densidade: float
    linhas: int | None = None            # linhas da tabela quando a estatistica foi calculada
    ultima_atualizacao: str | None = None
    modificacoes: int | None = None      # linhas modificadas desde a ultima atualizacao
    comprimento_medio: float | None = None   # "Average Length" do vetor de densidade (bytes, 1a coluna); semantica NAO verificada contra um servidor


def _limpar(nome: str) -> str:
    return nome.replace("[", "").replace("]", "").strip()


def chave_tabela(nome: str) -> tuple[str | None, str]:
    """('dbo', 'posts') a partir de 'dbo.Posts', '[dbo].[Posts]' ou 'Posts' -> (None, 'posts'); minusculas, sem colchetes."""
    partes = [p for p in _limpar(nome).lower().split(".") if p]
    if not partes:
        raise ValueError(f"nome de tabela vazio: {nome!r}")
    return (partes[-2] if len(partes) >= 2 else None), partes[-1]


@dataclass
class EsquemaBanco:
    origem: str = ""
    banco: str | None = None
    colacao_banco: str | None = None
    colunas: list[ColunaEsq] = field(default_factory=list)
    tabelas: list[TabelaEsq] = field(default_factory=list)
    indices: list[Indice] = field(default_factory=list)
    densidades: list[DensidadeEsq] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------ consulta
    def buscar_tabela(self, nome: str) -> TabelaEsq | None:
        """Tabela por nome ('Posts' ou 'dbo.Posts'). Nome simples ambiguo entre esquemas devolve None (nunca escolhe)."""
        esq, tab = chave_tabela(nome)
        achadas = [t for t in self.tabelas if t.tabela.lower() == tab and (esq is None or t.esquema.lower() == esq)]
        return achadas[0] if len(achadas) == 1 else None

    def _da_tabela(self, itens, nome: str) -> list:
        t = self.buscar_tabela(nome)
        if t is None:
            return []
        return [i for i in itens if i.esquema == t.esquema and i.tabela == t.tabela]

    def colunas_da(self, nome: str) -> list[ColunaEsq]:
        return sorted(self._da_tabela(self.colunas, nome), key=lambda c: c.ordem)

    def coluna(self, nome_tabela: str, coluna: str) -> ColunaEsq | None:
        return next((c for c in self.colunas_da(nome_tabela) if c.coluna.lower() == _limpar(coluna).lower()), None)

    def indices_da(self, nome: str) -> list[Indice]:
        return self._da_tabela(self.indices, nome)

    def indice_clusterizado(self, nome: str) -> Indice | None:
        return next((i for i in self.indices_da(nome) if i.clusterizado), None)

    def chaves_unicas(self, nome: str) -> list[tuple[str, ...]]:
        """Conjuntos de colunas que identificam uma linha: chaves primarias e indices unicos (so as colunas-chave)."""
        return [i.chave for i in self.indices_da(nome) if i.pk or i.unico]

    def linhas(self, nome: str) -> int | None:
        t = self.buscar_tabela(nome)
        return t.linhas if t else None

    def densidade(self, nome: str, coluna: str) -> DensidadeEsq | None:
        t = self.buscar_tabela(nome)
        if t is None:
            return None
        achadas = [d for d in self._da_tabela(self.densidades, nome) if d.coluna.lower() == _limpar(coluna).lower()]
        return achadas[0] if achadas else None

    def distintos(self, nome: str, coluna: str) -> int | None:
        """Valores distintos estimados pela densidade (1 / all density); None sem densidade."""
        d = self.densidade(nome, coluna)
        return round(1.0 / d.densidade) if d and d.densidade and d.densidade > 0 else None

    def seletividade_coluna(self, nome: str, coluna: str) -> float | None:
        """Seletividade da COLUNA no sentido do projeto: distintos / total de linhas. None se nao houver densidade e
        contagem de linhas; nunca inventada."""
        n, total = self.distintos(nome, coluna), self.linhas(nome)
        if not n or not total:
            return None
        return min(1.0, n / total)

    # ------------------------------------------------------------------ serializacao
    def para_dict(self) -> dict:
        planas = []
        for i in self.indices:
            for k, c in enumerate(i.chave, 1):
                planas.append(_linha_indice(i, k, False, c))
            for c in i.incluidas:
                planas.append(_linha_indice(i, 0, True, c))
        return {
            "versao": VERSAO_FORMATO, "origem": self.origem, "banco": self.banco, "colacao_banco": self.colacao_banco,
            "colunas": [asdict(c) for c in self.colunas], "tabelas": [asdict(t) for t in self.tabelas],
            "indices": planas, "densidades": [asdict(d) for d in self.densidades], "avisos": list(self.avisos),
        }

    def para_json(self) -> str:
        return json.dumps(self.para_dict(), ensure_ascii=False, indent=1)

    @classmethod
    def de_dict(cls, d: dict) -> "EsquemaBanco":
        colunas = [ColunaEsq(c["esquema"], c["tabela"], int(c["ordem"]), c["coluna"], c["tipo"], int(c["bytes"]),
                             bool(c["anulavel"]), bool(c.get("identidade", False)), c.get("colacao")) for c in d.get("colunas", [])]
        tabelas = [TabelaEsq(t["esquema"], t["tabela"], int(t["linhas"]), int(t["paginas"])) for t in d.get("tabelas", [])]
        grupos: dict[tuple, dict] = {}
        for r in d.get("indices", []):
            g = grupos.setdefault((r["esquema"], r["tabela"], r["indice"]), {"r": r, "chave": [], "incl": []})
            if r.get("incluida"):
                g["incl"].append(r["coluna"])
            else:
                g["chave"].append((int(r.get("ordem_chave") or 0), r["coluna"]))
        indices = [
            Indice(k[0], k[1], k[2], g["r"]["tipo"], bool(g["r"].get("unico")), bool(g["r"].get("pk")),
                   tuple(c for _, c in sorted(g["chave"], key=lambda x: x[0])), tuple(g["incl"]))
            for k, g in grupos.items()
        ]
        dens = [DensidadeEsq(x["esquema"], x["tabela"], x["estatistica"], x["coluna"], float(x["densidade"]), x.get("linhas"),
                             x.get("ultima_atualizacao"), x.get("modificacoes"), x.get("comprimento_medio")) for x in d.get("densidades", [])]
        return cls(d.get("origem", ""), d.get("banco"), d.get("colacao_banco"), colunas, tabelas, indices, dens, list(d.get("avisos", [])))


def _linha_indice(i: Indice, ordem: int, incluida: bool, coluna: str) -> dict:
    return {"esquema": i.esquema, "tabela": i.tabela, "indice": i.nome, "tipo": i.tipo, "unico": i.unico, "pk": i.pk,
            "ordem_chave": ordem, "incluida": incluida, "coluna": coluna}


def salvar(esquema: EsquemaBanco, caminho: str | Path) -> None:
    Path(caminho).write_text(esquema.para_json() + "\n", encoding="utf-8")


def carregar(caminho: str | Path) -> EsquemaBanco:
    return EsquemaBanco.de_dict(json.loads(Path(caminho).read_text(encoding="utf-8")))


# ----------------------------------------------------------------------------------------------- leitura no banco
_SQL_COLUNAS = (
    "SELECT s.name, t.name, c.column_id, c.name, ty.name, c.max_length, c.is_nullable, c.is_identity, c.collation_name "
    "FROM sys.columns c JOIN sys.tables t ON t.object_id = c.object_id JOIN sys.schemas s ON s.schema_id = t.schema_id "
    "JOIN sys.types ty ON ty.user_type_id = c.user_type_id WHERE t.is_ms_shipped = 0 ORDER BY s.name, t.name, c.column_id"
)
_SQL_TABELAS = (
    "SELECT s.name, t.name, SUM(CASE WHEN ps.index_id IN (0, 1) THEN ps.row_count ELSE 0 END), "
    "SUM(CASE WHEN ps.index_id IN (0, 1) THEN ps.used_page_count ELSE 0 END) "
    "FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
    "JOIN sys.dm_db_partition_stats ps ON ps.object_id = t.object_id WHERE t.is_ms_shipped = 0 "
    "GROUP BY s.name, t.name ORDER BY s.name, t.name"
)
_SQL_INDICES = (
    "SELECT s.name, t.name, i.name, i.type_desc, i.is_unique, i.is_primary_key, ic.key_ordinal, ic.is_included_column, c.name "
    "FROM sys.indexes i JOIN sys.tables t ON t.object_id = i.object_id JOIN sys.schemas s ON s.schema_id = t.schema_id "
    "JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
    "JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
    "WHERE t.is_ms_shipped = 0 AND i.type > 0 ORDER BY s.name, t.name, i.index_id, ic.is_included_column, ic.key_ordinal, ic.index_column_id"
)
_SQL_ESTATISTICAS = (
    "SELECT s.name, t.name, st.name, c.name, sp.rows, CONVERT(varchar(30), sp.last_updated, 126), sp.modification_counter "
    "FROM sys.stats st JOIN sys.tables t ON t.object_id = st.object_id JOIN sys.schemas s ON s.schema_id = t.schema_id "
    "JOIN sys.stats_columns sc ON sc.object_id = st.object_id AND sc.stats_id = st.stats_id AND sc.stats_column_id = 1 "
    "JOIN sys.columns c ON c.object_id = sc.object_id AND c.column_id = sc.column_id "
    "CROSS APPLY sys.dm_db_stats_properties(st.object_id, st.stats_id) sp WHERE t.is_ms_shipped = 0 ORDER BY s.name, t.name, st.stats_id"
)
_SQL_BANCO = "SELECT DB_NAME(), CONVERT(nvarchar(128), DATABASEPROPERTYEX(DB_NAME(), 'Collation'))"


def _linhas(con: Conexao, sql: str) -> list[tuple]:
    r = con.executar_lote(sql, guardar_linhas=_LIMITE_LINHAS)
    return list(r.conjuntos[0].linhas) if r.conjuntos else []


def _lit(nome: str) -> str:
    return "N'" + nome.replace("'", "''") + "'"


def inspecionar(con: Conexao, *, com_densidades: bool = True, origem: str = "") -> EsquemaBanco:
    """Le os metadados do banco da conexao. Somente SELECT em catalogos e `DBCC SHOW_STATISTICS ... WITH DENSITY_VECTOR`
    (leitura do vetor de densidade; nao atualiza estatistica). Falha numa estatistica vira aviso, nao erro."""
    banco, colacao = None, None
    linhas = _linhas(con, _SQL_BANCO)
    if linhas:
        banco, colacao = linhas[0][0], linhas[0][1]
    colunas = [ColunaEsq(r[0], r[1], int(r[2]), r[3], r[4], int(r[5]), bool(r[6]), bool(r[7]), r[8]) for r in _linhas(con, _SQL_COLUNAS)]
    tabelas = [TabelaEsq(r[0], r[1], int(r[2] or 0), int(r[3] or 0)) for r in _linhas(con, _SQL_TABELAS)]
    grupos: dict[tuple, dict] = {}
    for r in _linhas(con, _SQL_INDICES):
        g = grupos.setdefault((r[0], r[1], r[2]), {"tipo": r[3], "unico": bool(r[4]), "pk": bool(r[5]), "chave": [], "incl": []})
        (g["incl"] if r[7] else g["chave"]).append(r[8])
    indices = [Indice(k[0], k[1], k[2], g["tipo"], g["unico"], g["pk"], tuple(g["chave"]), tuple(g["incl"])) for k, g in grupos.items()]
    esquema = EsquemaBanco(origem or f"{banco or 'banco'} (inspecionar)", banco, colacao, colunas, tabelas, indices)
    if com_densidades:
        _ler_densidades(con, esquema)
    return esquema


def _ler_densidades(con: Conexao, esquema: EsquemaBanco) -> None:
    for r in _linhas(con, _SQL_ESTATISTICAS):
        esq, tab, est, col = r[0], r[1], r[2], r[3]
        alvo = f"[{esq}].[{tab}]"
        try:
            vetor = _linhas(con, f"DBCC SHOW_STATISTICS ({_lit(alvo)}, {_lit(est)}) WITH DENSITY_VECTOR")
        except Exception as e:   # estatistica sem vetor legivel: registra e segue
            esquema.avisos.append(f"densidade de {alvo}.{est} nao lida: {str(e)[:120]}")
            continue
        dens = float(vetor[0][0]) if vetor and vetor[0] and vetor[0][0] is not None else None
        medio = float(vetor[0][1]) if vetor and len(vetor[0]) > 1 and vetor[0][1] is not None else None
        if not dens or dens <= 0:
            esquema.avisos.append(f"densidade de {alvo}.{est} ausente ou zero: seletividade da coluna {col} nao determinada")
            continue
        esquema.densidades.append(DensidadeEsq(esq, tab, est, col, dens, int(r[4]) if r[4] is not None else None,
                                               r[5], int(r[6]) if r[6] is not None else None, medio))


# ----------------------------------------------------------------------------------------------- linha de comando
def principal(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Instantaneo dos metadados de um banco em JSON (conecta ao SQL Server; so leitura de catalogos)")
    p.add_argument("--banco", required=True, help="banco a inspecionar")
    p.add_argument("--saida", required=True, help="arquivo JSON de saida")
    p.add_argument("--sem-densidades", action="store_true", help="nao le o vetor de densidade das estatisticas")
    a = p.parse_args(argv)
    from .config import Config
    from .driver import conectar

    con = conectar(Config.carregar(), a.banco)
    try:
        esq = inspecionar(con, com_densidades=not a.sem_densidades)
    finally:
        con.fechar()
    salvar(esq, a.saida)
    print(f"{len(esq.tabelas)} tabelas, {len(esq.colunas)} colunas, {len(esq.indices)} indices, {len(esq.densidades)} densidades em {a.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(principal())
