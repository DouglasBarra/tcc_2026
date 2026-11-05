"""Analisador das mensagens de SET STATISTICS IO e SET STATISTICS TIME.

Formatos observados no SQL Server 2022 (CU27), idioma us_english (ver relatorio 19):

  3615  Table 'm0_sanidade'. Scan count 1, logical reads 5737, physical reads 0,
        page server reads 0, read-ahead reads 0, page server read-ahead reads 0,
        lob logical reads 0, lob physical reads 0, lob page server reads 0,
        lob read-ahead reads 0, lob page server read-ahead reads 0.
  3613  SQL Server parse and compile time:
           CPU time = 0 ms, elapsed time = 0 ms.
  3612   SQL Server Execution Times:
           CPU time = 14 ms,  elapsed time = 13 ms.

A classificacao usa o NUMERO da mensagem (independe do idioma; testado com us_english, Brazilian,
German e French). O TEXTO e traduzido pelo servidor quando a sessao usa outro idioma. Por isso:
  - em ingles, os campos sao lidos pelo rotulo;
  - em outros idiomas, cai para leitura POSICIONAL (a ordem dos 11 contadores de IO e fixa; no tempo,
    os dois primeiros inteiros sao CPU e decorrido).
O nucleo ainda fixa `SET LANGUAGE us_english` na sessao (medir.SESSAO_PADRAO).
Se o driver nao informar o numero (ex.: pymssql), cai para regex no texto em ingles.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .driver import MensagemServidor

MSG_TEMPO_EXEC = 3612
MSG_TEMPO_COMPILE = 3613
MSG_IO = 3615

_RE_TEMPO = re.compile(r"CPU time\s*=\s*(\d+)\s*ms\s*,\s*elapsed time\s*=\s*(\d+)\s*ms", re.I)
_RE_TABELA = re.compile(r"^\s*Table\s+'(?P<tabela>.*?)'\.\s*(?P<resto>.*)$", re.I | re.S)
# qualquer idioma: primeira palavra, depois nome entre aspas simples ou tipograficas (' ' „ “ ”)
_RE_TABELA_GENERICA = re.compile(r"^\s*[^'„“”]*?['„“”](?P<tabela>[^'„“”]+)['“”]\s*[.:]?\s*(?P<resto>.*)$", re.S)
_ORDEM_POSICIONAL = [
    "scan_count", "logical_reads", "physical_reads", "page_server_reads", "read_ahead_reads",
    "page_server_read_ahead_reads", "lob_logical_reads", "lob_physical_reads", "lob_page_server_reads",
    "lob_read_ahead_reads", "lob_page_server_read_ahead_reads",
]

_ROTULOS = {
    "scan count": "scan_count",
    "logical reads": "logical_reads",
    "physical reads": "physical_reads",
    "page server reads": "page_server_reads",
    "read-ahead reads": "read_ahead_reads",
    "page server read-ahead reads": "page_server_read_ahead_reads",
    "lob logical reads": "lob_logical_reads",
    "lob physical reads": "lob_physical_reads",
    "lob page server reads": "lob_page_server_reads",
    "lob read-ahead reads": "lob_read_ahead_reads",
    "lob page server read-ahead reads": "lob_page_server_read_ahead_reads",
}


@dataclass
class IOTabela:
    tabela: str
    scan_count: int = 0
    logical_reads: int = 0
    physical_reads: int = 0
    page_server_reads: int = 0
    read_ahead_reads: int = 0
    page_server_read_ahead_reads: int = 0
    lob_logical_reads: int = 0
    lob_physical_reads: int = 0
    lob_page_server_reads: int = 0
    lob_read_ahead_reads: int = 0
    lob_page_server_read_ahead_reads: int = 0


@dataclass
class Tempo:
    cpu_ms: int
    elapsed_ms: int


@dataclass
class EstatisticasLote:
    io: list[IOTabela] = field(default_factory=list)
    execucao: list[Tempo] = field(default_factory=list)   # um por instrucao (3612)
    compilacao: list[Tempo] = field(default_factory=list)  # um por instrucao (3613)
    io_bruto: str = ""
    tempo_bruto: str = ""

    def soma(self, campo: str) -> int:
        return sum(getattr(t, campo) for t in self.io)

    @property
    def cpu_ms(self) -> int:
        return sum(t.cpu_ms for t in self.execucao)

    @property
    def elapsed_ms(self) -> int:
        return sum(t.elapsed_ms for t in self.execucao)

    @property
    def compile_cpu_ms(self) -> int:
        return sum(t.cpu_ms for t in self.compilacao)

    @property
    def compile_elapsed_ms(self) -> int:
        return sum(t.elapsed_ms for t in self.compilacao)


def parse_io(texto: str) -> IOTabela | None:
    """Le uma mensagem 3615. Rotulos em ingles; em outro idioma, leitura posicional."""
    m = _RE_TABELA.match(texto.strip())
    if m:
        reg = IOTabela(tabela=m.group("tabela"))
        achou = 0
        for pedaco in m.group("resto").rstrip(". \n").split(","):
            mm = re.match(r"^\s*(?P<rot>.+?)\s+(?P<n>\d+)\s*$", pedaco)
            campo = _ROTULOS.get(mm.group("rot").strip().lower()) if mm else None
            if campo:
                setattr(reg, campo, int(mm.group("n")))
                achou += 1
        if achou:
            return reg
    g = _RE_TABELA_GENERICA.match(texto.strip())
    if not g:
        return None
    numeros = [int(x) for x in re.findall(r"\d+", g.group("resto"))]
    if len(numeros) != len(_ORDEM_POSICIONAL):
        return None  # formato diferente do SQL Server 2022: nao arrisca
    reg = IOTabela(tabela=g.group("tabela"))
    for campo, n in zip(_ORDEM_POSICIONAL, numeros):
        setattr(reg, campo, n)
    return reg


def parse_tempo(texto: str) -> Tempo | None:
    m = _RE_TEMPO.search(texto)
    if m:
        return Tempo(int(m.group(1)), int(m.group(2)))
    nums = re.findall(r"=\s*(\d+)\s*ms", texto)  # outros idiomas: "... = 3 ms, ... = 4 ms"
    return Tempo(int(nums[0]), int(nums[1])) if len(nums) >= 2 else None


def analisar(mensagens: list[MensagemServidor]) -> EstatisticasLote:
    """Converte as mensagens de um lote em estatisticas. Ignora mensagens que nao sao de STATISTICS."""
    est = EstatisticasLote()
    io_txt: list[str] = []
    tempo_txt: list[str] = []
    for msg in mensagens:
        texto = msg.texto
        numero = msg.numero
        if numero is None:  # driver sem numero de mensagem: heuristica pelo texto (ingles)
            if re.match(r"\s*Table\s+'", texto, re.I):
                numero = MSG_IO
            elif "parse and compile time" in texto.lower():
                numero = MSG_TEMPO_COMPILE
            elif "execution times" in texto.lower():
                numero = MSG_TEMPO_EXEC
        if numero == MSG_IO:
            reg = parse_io(texto)
            if reg:
                est.io.append(reg)
                io_txt.append(texto.strip())
        elif numero == MSG_TEMPO_COMPILE:
            t = parse_tempo(texto)
            if t:
                est.compilacao.append(t)
                tempo_txt.append(texto.strip())
        elif numero == MSG_TEMPO_EXEC:
            t = parse_tempo(texto)
            if t:
                est.execucao.append(t)
                tempo_txt.append(texto.strip())
    est.io_bruto = "\n".join(io_txt)
    est.tempo_bruto = "\n".join(tempo_txt)
    return est
