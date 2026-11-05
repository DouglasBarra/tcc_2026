"""Captura saidas brutas reais (mensagens de STATISTICS e planos XML) para os testes de unidade.
Uso: .venv/bin/python scripts/gerar_fixtures.py   (exige o SQL Server de pratica/infra rodando)"""
import json
from pathlib import Path

from tcclab.canais import capturar_plano_estimado, capturar_plano_real
from tcclab.driver import conectar
from tcclab.medir import preparar_sessao

DEST = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
SQL = "SELECT COUNT(*), SUM(valor) FROM dbo.m0_sanidade WHERE cliente = 42"
con = conectar()
preparar_sessao(con)
con.executar_lote(SQL)
r = con.executar_lote(SQL)
(DEST / "mensagens_scan.json").write_text(
    json.dumps([{"numero": m.numero, "texto": m.texto} for m in r.mensagens], ensure_ascii=False, indent=1), encoding="utf-8")
xml, _ = capturar_plano_real(con, SQL)
(DEST / "plano_real_scan.sqlplan").write_text(xml, encoding="utf-8")
xml, _ = capturar_plano_estimado(con, SQL)
(DEST / "plano_estimado_scan.sqlplan").write_text(xml, encoding="utf-8")
con.executar_lote("CREATE INDEX ix_m0_fx ON dbo.m0_sanidade(cliente) INCLUDE (valor)")
try:
    con.executar_lote("ALTER DATABASE SCOPED CONFIGURATION CLEAR PROCEDURE_CACHE")
    xml, _ = capturar_plano_real(con, SQL)
    (DEST / "plano_real_seek.sqlplan").write_text(xml, encoding="utf-8")
    con.executar_lote(SQL)
    r = con.executar_lote(SQL)
    (DEST / "mensagens_seek.json").write_text(
        json.dumps([{"numero": m.numero, "texto": m.texto} for m in r.mensagens], ensure_ascii=False, indent=1), encoding="utf-8")
finally:
    con.executar_lote("DROP INDEX ix_m0_fx ON dbo.m0_sanidade")
con.fechar()
print("ok", sorted(p.name for p in DEST.iterdir()))
