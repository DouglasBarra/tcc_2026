"""CONEXAO FALSA, SINTETICA, SO PARA TESTES.

Implementa a interface `tcclab.driver.Conexao` sem tocar em nenhum SQL Server. Os numeros que devolve
(leituras logicas 5737 sem indice e 3 com indice, CPU 13 ms e 0 ms) sao FABRICADOS por este arquivo; o unico
conteudo "real" sao os tres planos XML em tests/fixtures, copiados de pratica/nucleo/tests/fixtures
(capturas do M0). Nada daqui aparece na interface: a UI so le SQLite gravado por tcclab.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from tcclab.driver import ConjuntoResultado, MensagemServidor, ResultadoLote

FIXTURES = Path(__file__).parent / "fixtures"
COLUNA_PLANO = "Microsoft SQL Server 2005 XML Showplan"
HASH_PLANO_SCAN = "0xE3CB399B574BC0E8"
HASH_PLANO_SEEK = "0x1BC93402BC66BCC1"
HASH_CONSULTA = "0xA3F8017680B6E2D8"
LEITURAS_SEM_INDICE = 5737      # sintetico
LEITURAS_COM_INDICE = 3         # sintetico


def _msgs(lr: int, cpu: int, el: int) -> list[MensagemServidor]:
    io = (f"Table 'sintetica'. Scan count 1, logical reads {lr}, physical reads 0, page server reads 0, read-ahead reads 0, "
          "page server read-ahead reads 0, lob logical reads 0, lob physical reads 0, lob page server reads 0, "
          "lob read-ahead reads 0, lob page server read-ahead reads 0.")
    return [
        MensagemServidor(3613, 0, 1, "SQL Server parse and compile time: \n   CPU time = 0 ms, elapsed time = 0 ms."),
        MensagemServidor(3615, 0, 1, io),
        MensagemServidor(3612, 0, 1, f"\n SQL Server Execution Times:\n   CPU time = {cpu} ms,  elapsed time = {el} ms."),
    ]


def _res(conjuntos=None, mensagens=None) -> ResultadoLote:
    return ResultadoLote(conjuntos=conjuntos or [], mensagens=mensagens or [], cliente_ms=0.1)


def _linhas(colunas: list[str], linhas: list[tuple]) -> ResultadoLote:
    return _res([ConjuntoResultado(colunas=colunas, total_linhas=len(linhas), linhas=linhas)])


class FakeConexao:
    def __init__(self, *, banco: str = "so_exp", bloquear: threading.Event | None = None,
                 iniciou: threading.Event | None = None, divergir_se: str | None = None,
                 falhar_se: str | None = None, mensagens_planas: bool = False):
        self.banco = banco
        self.bloquear = bloquear
        self.iniciou = iniciou
        self.divergir_se = divergir_se
        self.falhar_se = falhar_se
        self.indice_aplicado = False
        self.io = self.time = self.xml = self.showplan = False
        self.chamadas: list[str] = []
        self.fechada = False
        self._execs: dict[str, int] = {}
        self._primeira = True

    # --- estado simulado
    def _plano_hash(self) -> str:
        return HASH_PLANO_SEEK if self.indice_aplicado else HASH_PLANO_SCAN

    def _lr(self) -> int:
        return LEITURAS_COM_INDICE if self.indice_aplicado else LEITURAS_SEM_INDICE

    def _cpu_ms(self) -> int:
        return 0 if self.indice_aplicado else 13

    def _cpu_us(self) -> int:
        return 40 if self.indice_aplicado else 13_000

    def executar_lote(self, sql: str, *, guardar_linhas: int = 0, hash_linhas: bool = False,
                      hash_multiconjunto: bool = False) -> ResultadoLote:
        if self._primeira:
            self._primeira = False
            if self.iniciou:
                self.iniciou.set()
            if self.bloquear:
                self.bloquear.wait(timeout=30)
        self.chamadas.append(sql)
        s = " ".join(sql.split())
        u = s.upper()
        if self.falhar_se and self.falhar_se in sql:
            raise RuntimeError("falha simulada do servidor")

        # --- opcoes de sessao
        if u.startswith("SET "):
            on = u.endswith(" ON")
            if "STATISTICS IO" in u:
                self.io = on
            elif "STATISTICS TIME" in u:
                self.time = on
            elif "STATISTICS XML" in u:
                self.xml = on
            elif "SHOWPLAN_XML" in u:
                self.showplan = on
            return _res()
        # --- comandos de estado
        if u.startswith(("CHECKPOINT", "DBCC ", "ALTER DATABASE", "UPDATE STATISTICS", "EXEC SYS.SP_QUERY_STORE")):
            return _res()
        if u.startswith("CREATE INDEX"):
            self.indice_aplicado = True
            return _res()
        if u.startswith("DROP INDEX"):
            self.indice_aplicado = False
            return _res()
        # --- ambiente
        if "@@VERSION" in u:
            return _linhas(["v"], [("Microsoft SQL Server 2022 (SINTETICO-TESTE)\n\tcopyright",)])
        if "SERVERPROPERTY('EDITION')" in u:
            return _linhas(["e", "p"], [("Developer Edition (64-bit)", "16.0.0.0")])
        if "COMPATIBILITY_LEVEL FROM SYS.DATABASES" in u:
            return _linhas(["c"], [(160,)])
        if "SYS.DM_OS_SYS_INFO" in u:
            return _linhas(["a", "b"], [(15, 4096)])
        if "SYS.DATABASE_SCOPED_CONFIGURATIONS" in u:
            return _linhas(["n", "v"], [("MAXDOP", "1"), ("LEGACY_CARDINALITY_ESTIMATION", "0")])
        if "SYS.CONFIGURATIONS" in u:
            return _linhas(["n", "v"], [("max server memory (MB)", 4096), ("max degree of parallelism", 0), ("cost threshold for parallelism", 5)])
        if "SYS.DM_OS_HOST_INFO" in u:
            return _linhas(["a", "b"], [("Linux", "Ubuntu")])
        if "SYS.DATABASE_QUERY_STORE_OPTIONS" in u:
            return _linhas(["a"] * 5, [("READ_WRITE", "ALL", 1, 60, 512)])
        if "SYS.DM_EXEC_REQUESTS" in u or "STATE_DESC <> 'ONLINE'" in u:
            return _linhas(["x"], [])
        # --- canais D e C
        if "SYS.DM_EXEC_QUERY_STATS" in u:
            ph = self._plano_hash()
            n = self._execs.get(ph, 0)
            if "SELECT TOP 1" in u:
                if not n:
                    return _linhas(["x"], [])
                return _linhas(["a"] * 5, [(self._cpu_us(), self._cpu_us() + 5, self._lr(), 0, n)])
            linhas = [(h, c, c * (LEITURAS_COM_INDICE if h == HASH_PLANO_SEEK else LEITURAS_SEM_INDICE), 0,
                       c * (40 if h == HASH_PLANO_SEEK else 13_000), c * (45 if h == HASH_PLANO_SEEK else 13_005),
                       40 if h == HASH_PLANO_SEEK else 13_000, 40 if h == HASH_PLANO_SEEK else 13_000,
                       45 if h == HASH_PLANO_SEEK else 13_005, 45 if h == HASH_PLANO_SEEK else 13_005,
                       40 if h == HASH_PLANO_SEEK else 13_000, 45 if h == HASH_PLANO_SEEK else 13_005)
                      for h, c in self._execs.items()]
            return _linhas(["a"] * 12, linhas)
        if "SYS.QUERY_STORE_RUNTIME_STATS" in u:
            linhas = []
            for i, (h, c) in enumerate(self._execs.items(), start=1):
                seek = h == HASH_PLANO_SEEK
                linhas.append((i, i, h, c, c * (45 if seek else 13_005), c * (40 if seek else 13_000),
                               c * (LEITURAS_COM_INDICE if seek else LEITURAS_SEM_INDICE), 0, 1))
            return _linhas(["a"] * 9, linhas)

        # --- consulta do usuario
        if self.showplan:
            return _res([ConjuntoResultado(colunas=[COLUNA_PLANO], total_linhas=1,
                                           linhas=[((FIXTURES / "plano_estimado_scan.sqlplan").read_text(encoding="utf-8"),)])])
        ph = self._plano_hash()
        self._execs[ph] = self._execs.get(ph, 0) + 1
        marca = "divergente" if (self.divergir_se and self.divergir_se in sql) else "igual"
        dados = ConjuntoResultado(colunas=["N"], total_linhas=1, linhas=[(1,)][:guardar_linhas],
                                  hash_linhas=f"h-{marca}" if hash_linhas else None,
                                  hash_multiconjunto=f"{marca}:1" if hash_multiconjunto else None)
        conjuntos = [dados]
        if self.xml:
            arq = "plano_real_seek.sqlplan" if self.indice_aplicado else "plano_real_scan.sqlplan"
            conjuntos.append(ConjuntoResultado(colunas=[COLUNA_PLANO], total_linhas=1,
                                               linhas=[((FIXTURES / arq).read_text(encoding="utf-8"),)]))
        msgs = _msgs(self._lr(), self._cpu_ms(), self._cpu_ms()) if (self.io or self.time) else []
        return _res(conjuntos, msgs)

    def fechar(self) -> None:
        self.fechada = True
