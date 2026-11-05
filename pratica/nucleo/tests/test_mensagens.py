"""Unidade: analisador de STATISTICS IO/TIME sobre saidas brutas reais (tests/fixtures)."""
import json
from pathlib import Path

import pytest

from tcclab.driver import MensagemServidor
from tcclab.mensagens import analisar, parse_io, parse_tempo

FX = Path(__file__).parent / "fixtures"


def _msgs(nome):
    return [MensagemServidor(m["numero"], 0, 0, m["texto"]) for m in json.loads((FX / nome).read_text(encoding="utf-8"))]


def test_scan_leituras_e_tempo():
    est = analisar(_msgs("mensagens_scan.json"))
    assert est.soma("logical_reads") == 5737
    assert est.soma("scan_count") == 1
    assert [t.tabela for t in est.io] == ["m0_sanidade"]
    assert est.cpu_ms >= 0 and est.elapsed_ms >= 0
    assert len(est.compilacao) == 2  # consulta parametrizada automaticamente gera duas mensagens 3613


def test_seek_tres_leituras():
    est = analisar(_msgs("mensagens_seek.json"))
    assert est.soma("logical_reads") == 3
    assert est.cpu_ms == 0 and est.elapsed_ms == 0  # resolucao de 1 ms: consulta rapida vira 0


def test_texto_ingles_do_io():
    t = ("Table 'Worktable'. Scan count 0, logical reads 0, physical reads 0, page server reads 0, read-ahead reads 0, "
         "page server read-ahead reads 0, lob logical reads 12, lob physical reads 1, lob page server reads 0, "
         "lob read-ahead reads 3, lob page server read-ahead reads 0.")
    r = parse_io(t)
    assert (r.tabela, r.lob_logical_reads, r.lob_physical_reads, r.lob_read_ahead_reads) == ("Worktable", 12, 1, 3)


def test_parse_tempo():
    t = parse_tempo("\n SQL Server Execution Times:\n   CPU time = 14 ms,  elapsed time = 13 ms.")
    assert (t.cpu_ms, t.elapsed_ms) == (14, 13)


@pytest.mark.parametrize("idioma", ["Brazilian", "German", "French"])
def test_outros_idiomas_por_numero_e_posicao(idioma):
    """O texto e traduzido (SET LANGUAGE), mas o numero da mensagem e a ordem dos campos nao mudam."""
    brutos = json.loads((FX / "mensagens_localizadas.json").read_text(encoding="utf-8"))[idioma]
    est = analisar([MensagemServidor(m["numero"], 0, 0, m["texto"]) for m in brutos])
    assert est.soma("logical_reads") == 5737
    assert est.soma("scan_count") == 1
    assert est.io[0].tabela == "m0_sanidade"
    assert est.elapsed_ms > 0


def test_sem_numero_do_driver_usa_texto_em_ingles():
    ms = [MensagemServidor(None, 0, 0, m.texto) for m in _msgs("mensagens_scan.json")]
    assert analisar(ms).soma("logical_reads") == 5737


def test_ignora_mensagens_que_nao_sao_statistics():
    est = analisar([MensagemServidor(50000, 0, 0, "PRINT qualquer")])
    assert not est.io and not est.execucao
