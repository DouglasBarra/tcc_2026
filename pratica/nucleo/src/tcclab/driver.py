"""Interface fina de acesso ao SQL Server (trocavel) e implementacao com python-tds (pytds).

O resto do nucleo so conhece `Conexao`, `ResultadoLote`, `ConjuntoResultado` e
`MensagemServidor`. Trocar de driver = escrever outra classe que devolva essas
estruturas (a avaliacao dos drivers esta em knowledge/19-nucleo-medicao-m0.md).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Protocol

from .config import Config


@dataclass(frozen=True)
class MensagemServidor:
    """Mensagem informativa do servidor (PRINT, STATISTICS IO/TIME...).

    `numero` e o numero da mensagem do SQL Server: 3612 = STATISTICS TIME (execucao),
    3613 = STATISTICS TIME (parse/compile), 3615 = STATISTICS IO. Independe do idioma.
    """
    numero: int | None
    gravidade: int
    estado: int
    texto: str


@dataclass
class ConjuntoResultado:
    colunas: list[str]
    total_linhas: int
    linhas: list[tuple] = field(default_factory=list)  # so as primeiras `guardar_linhas`
    hash_linhas: str | None = None                       # sha256 ordenado-por-chegada, se pedido
    hash_multiconjunto: str | None = None                # independe da ordem das linhas (equivalencia entre variantes)

    @property
    def eh_plano_xml(self) -> bool:
        return len(self.colunas) == 1 and "showplan" in self.colunas[0].lower()


@dataclass
class ResultadoLote:
    conjuntos: list[ConjuntoResultado]
    mensagens: list[MensagemServidor]
    cliente_ms: float  # tempo medido no cliente (inclui rede e envio das linhas); so informativo

    def planos_xml(self) -> list[str]:
        return [str(c.linhas[0][0]) for c in self.conjuntos if c.eh_plano_xml and c.linhas]


class Conexao(Protocol):
    def executar_lote(
        self, sql: str, *, guardar_linhas: int = 0, hash_linhas: bool = False, hash_multiconjunto: bool = False
    ) -> ResultadoLote: ...
    def fechar(self) -> None: ...


class ConexaoPytds:
    """Conexao via python-tds. Cada chamada envia o texto como lote TDS (SQL batch), nao como
    sp_executesql, entao `SET STATISTICS ...` vale para a sessao inteira."""

    def __init__(self, cfg: Config, database: str | None = None):
        import pytds  # import tardio: so exige o driver quem conecta

        self.cfg = cfg
        self.database = database or cfg.database
        self._c = pytds.connect(
            cfg.host,
            database=self.database,
            user=cfg.user,
            password=cfg.password,
            port=cfg.port,
            autocommit=True,
            login_timeout=cfg.login_timeout,
            timeout=cfg.query_timeout,
            appname="tcclab",
        )

    def executar_lote(
        self, sql: str, *, guardar_linhas: int = 0, hash_linhas: bool = False, hash_multiconjunto: bool = False
    ) -> ResultadoLote:
        cur = self._c.cursor()
        t0 = time.perf_counter()
        cur.execute(sql)
        conjuntos: list[ConjuntoResultado] = []
        while True:
            if cur.description:
                colunas = [d[0] for d in cur.description]
                eh_xml = len(colunas) == 1 and "showplan" in colunas[0].lower()
                conj = ConjuntoResultado(colunas=colunas, total_linhas=0)
                h = hashlib.sha256() if hash_linhas else None
                soma_mc = 0  # soma mod 2^128 dos resumos de cada linha: comutativa, conta duplicatas
                while True:
                    bloco = cur.fetchmany(2000)
                    if not bloco:
                        break
                    for linha in bloco:
                        conj.total_linhas += 1
                        if h is not None:
                            h.update(repr(linha).encode("utf-8"))
                        if hash_multiconjunto:
                            soma_mc = (soma_mc + int.from_bytes(hashlib.sha256(repr(linha).encode("utf-8")).digest()[:16], "big")) % (1 << 128)
                        if eh_xml or len(conj.linhas) < guardar_linhas:
                            conj.linhas.append(tuple(linha))
                conj.hash_linhas = h.hexdigest() if h else None
                if hash_multiconjunto:
                    conj.hash_multiconjunto = f"{soma_mc:032x}:{conj.total_linhas}"
                conjuntos.append(conj)
            if not cur.nextset():
                break
        cliente_ms = (time.perf_counter() - t0) * 1000.0
        mensagens = []
        for item in getattr(cur, "messages", None) or []:
            exc = item[1]
            mensagens.append(
                MensagemServidor(
                    numero=getattr(exc, "number", None),
                    gravidade=getattr(exc, "severity", 0) or 0,
                    estado=getattr(exc, "state", 0) or 0,
                    texto=getattr(exc, "text", None) or str(exc),
                )
            )
        cur.close()
        return ResultadoLote(conjuntos=conjuntos, mensagens=mensagens, cliente_ms=cliente_ms)

    def fechar(self) -> None:
        try:
            self._c.close()
        except Exception:
            pass


def conectar(cfg: Config | None = None, database: str | None = None) -> ConexaoPytds:
    """Ponto unico de criacao de conexao (trocar o driver = trocar esta funcao)."""
    return ConexaoPytds(cfg or Config.carregar(), database)
