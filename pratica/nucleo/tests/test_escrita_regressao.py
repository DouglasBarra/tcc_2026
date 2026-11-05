"""Regressao do defeito de 26/10/2026: UPDATE/DELETE com TOP (k) sem ORDER BY tocavam linhas diferentes conforme o
caminho de acesso (com o indice presente). Agora o alvo e um intervalo da chave clusterizada, igual com e sem indice,
e a expressao do UPDATE sempre altera o valor, inclusive em colunas anulaveis."""
from __future__ import annotations

from dataclasses import dataclass, field

from tcclab import escrita


@dataclass
class _Conj:
    linhas: list
    colunas: list = field(default_factory=lambda: ["c"])
    total_linhas: int = 0
    hash_linhas: str | None = None
    hash_multiconjunto: str | None = None
    eh_plano_xml: bool = False


@dataclass
class _Res:
    conjuntos: list
    mensagens: list = field(default_factory=list)
    cliente_ms: float = 0.0


class _ConexaoFalsa:
    """Responde so as consultas de metadados que `comandos_dml` faz; nunca executa DML."""

    def __init__(self, tipo: str, max_bytes: int, hi: int = 1361):
        self.tipo, self.max_bytes, self.hi = tipo, max_bytes, hi
        self.lotes: list[str] = []

    def executar_lote(self, sql: str, **_kw):
        self.lotes.append(sql)
        if "FROM sys.columns c JOIN sys.types t" in sql and "c.is_identity = 0" in sql:
            return _Res([_Conj([("Id2",), ("Nome",)])])
        if "SELECT t.name, c.max_length" in sql:
            return _Res([_Conj([(self.tipo, self.max_bytes)])])
        if sql.startswith("SELECT MAX(Id) FROM (SELECT TOP"):
            return _Res([_Conj([(self.hi,)])])
        raise AssertionError(f"consulta inesperada: {sql[:80]}")

    def fechar(self) -> None:
        pass


def test_update_e_delete_usam_o_mesmo_intervalo_da_chave_clusterizada():
    con = _ConexaoFalsa("int", 4)
    d = escrita.comandos_dml(con, "dbo.Posts", "CREATE INDEX ix ON dbo.Posts (OwnerUserId)", 1000)
    assert d["delete"] == "DELETE FROM dbo.Posts WHERE Id <= 1361"
    assert d["update"].endswith("WHERE Id <= 1361")
    assert "TOP" not in d["update"] and "TOP" not in d["delete"]  # TOP sem ORDER BY era o defeito
    assert "ORDER BY Id" in d["insert"]  # a insercao continua deterministica


def test_update_de_coluna_anulavel_sempre_muda_o_valor():
    inteiro = escrita._expressao_update(_ConexaoFalsa("int", 4), "dbo.Posts", "OwnerUserId")
    assert inteiro == "ISNULL([OwnerUserId], 0) + 1"  # NULL + 1 seria NULL: o UPDATE nao mexeria no indice
    data = escrita._expressao_update(_ConexaoFalsa("datetime", 8), "dbo.Votes", "CreationDate")
    assert "ISNULL([CreationDate]" in data and "DATEADD" in data


def test_update_de_texto_respeita_o_tamanho_maximo_da_coluna():
    # nvarchar(100): 200 bytes no catalogo; a expressao nao pode estourar o limite nem ser identidade
    expr = escrita._expressao_update(_ConexaoFalsa("nvarchar", 200), "dbo.Users", "Location")
    assert "LEN(ISNULL([Location], N'')) < 100" in expr
    assert "STUFF(" in expr  # ramo para coluna ja cheia
