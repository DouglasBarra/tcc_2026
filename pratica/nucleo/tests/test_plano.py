"""Unidade: leitura do plano XML (fixtures reais capturadas do SQL Server 2022 CU27)."""
from pathlib import Path

from tcclab.plano import analisar_plano

FX = Path(__file__).parent / "fixtures"


def _p(nome):
    return analisar_plano((FX / nome).read_text(encoding="utf-8")).principal()


def test_plano_real_scan():
    p = _p("plano_real_scan.sqlplan")
    assert p.eh_plano_real
    assert p.query_hash and p.query_hash.startswith("0x") and len(p.query_hash) == 18
    assert p.query_plan_hash and p.query_plan_hash.startswith("0x")
    scan = next(o for o in p.operadores if o.physical_op == "Clustered Index Scan")
    assert scan.actual_logical_reads == 5737
    assert scan.actual_rows_read == 200000
    assert scan.objeto.startswith("[dbo].[m0_sanidade]") and scan.tipo_indice == "Clustered"
    assert "cliente" in scan.predicado
    assert p.leituras_logicas_plano == 5737
    assert p.versao_ce == 160 and p.grau_paralelismo == 1


def test_plano_real_seek_menos_leituras_e_outro_hash():
    scan, seek = _p("plano_real_scan.sqlplan"), _p("plano_real_seek.sqlplan")
    assert seek.leituras_logicas_plano == 3
    assert any("Index Seek" in o.physical_op for o in seek.operadores)
    assert seek.query_hash == scan.query_hash          # mesma consulta
    assert seek.query_plan_hash != scan.query_plan_hash  # plano diferente
    assert seek.hash_formato != scan.hash_formato


def test_plano_estimado_nao_tem_contadores_reais():
    est, real = _p("plano_estimado_scan.sqlplan"), _p("plano_real_scan.sqlplan")
    assert not est.eh_plano_real and est.leituras_logicas_plano == 0
    assert est.query_plan_hash == real.query_plan_hash  # o hash do plano e o mesmo no estimado e no real
    assert est.hash_formato == real.hash_formato
