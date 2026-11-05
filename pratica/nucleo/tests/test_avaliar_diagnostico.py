"""Unidade: logica de pontuacao do conjunto de VALIDACAO (esperado/tolerado/proibido) em experimento/avaliar_diagnostico.py.

Usa um arquivo SINTETICO escrito aqui (consultas triviais de resultado conhecido), nunca o heldout.toml: o conjunto de validacao
roda uma unica vez, com o detector congelado (PRE-REGISTRO.md, secao 5).
"""
import csv
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

EXP = Path(__file__).resolve().parents[2] / "experimento"
_spec = importlib.util.spec_from_file_location("avaliar_diagnostico", EXP / "avaliar_diagnostico.py")
av = importlib.util.module_from_spec(_spec)
sys.modules["avaliar_diagnostico"] = av
_spec.loader.exec_module(av)

TODAS = '["AP1", "AP2", "AP3", "AP4"]'
TOML = f'''
[meta]
versao = 1
banco = "x"
dataset = "x"

[[consulta]]
id = "T1"
antipadrao = "AP1"
titulo = "funcao: acerto"
tabelas = ["t"]
seletividade = "alta"
esperado = ["AP1"]
tolerado = ["AP4"]
proibido = ["AP2", "AP3"]
sql_original = """SELECT a FROM t WHERE UPPER(b) = 'X'"""

[[consulta]]
id = "T2"
antipadrao = "AP3"
titulo = "esperado AP3 e AP4: em so SQL o AP4 e inatingivel"
tabelas = ["t"]
seletividade = "alta"
esperado = ["AP3", "AP4"]
tolerado = []
proibido = ["AP1", "AP2"]
sql_original = """SELECT * FROM t WHERE a = 1"""

[[consulta]]
id = "T3"
antipadrao = "NENHUM"
titulo = "limpa correta"
tabelas = ["t"]
seletividade = "alta"
esperado = []
tolerado = []
proibido = {TODAS}
sql_original = """SELECT a FROM t WHERE a = 1"""

[[consulta]]
id = "T4"
antipadrao = "NENHUM"
titulo = "rotulada limpa mas tem SELECT *: falso positivo proibido"
tabelas = ["t"]
seletividade = "alta"
esperado = []
tolerado = []
proibido = ["AP3"]
sql_original = """SELECT * FROM t"""

[[consulta]]
id = "T5"
antipadrao = "AP2"
titulo = "esperado AP2 mas nao ha subconsulta: omissao"
tabelas = ["t"]
seletividade = "alta"
esperado = ["AP2"]
tolerado = []
proibido = []
sql_original = """SELECT a FROM t"""

[[consulta]]
id = "T6"
antipadrao = "AP1"
titulo = "acerto com falso positivo (SELECT * nao previsto)"
tabelas = ["t"]
seletividade = "alta"
esperado = ["AP1"]
tolerado = []
proibido = ["AP2"]
sql_original = """SELECT * FROM t WHERE YEAR(d) = 2008"""

[[consulta]]
id = "T7"
antipadrao = "AP1"
titulo = "acerto com AP3 tolerado"
tabelas = ["t"]
seletividade = "alta"
esperado = ["AP1"]
tolerado = ["AP3"]
proibido = ["AP2"]
sql_original = """SELECT * FROM t WHERE LEFT(n, 3) = 'x'"""

[[consulta]]
id = "T8"
antipadrao = "AP1"
titulo = "omissao e falso positivo ao mesmo tempo"
tabelas = ["t"]
seletividade = "alta"
esperado = ["AP1"]
tolerado = []
proibido = []
sql_original = """SELECT * FROM t"""

[[consulta]]
id = "T9"
antipadrao = "AP1"
titulo = "SQL nao analisavel: nunca vira acerto"
tabelas = ["t"]
seletividade = "alta"
esperado = ["AP1"]
tolerado = []
proibido = []
sql_original = """SELEC * FRM x"""
'''


@pytest.fixture()
def sintetico(tmp_path):
    arq = tmp_path / "sintetico.toml"
    arq.write_text(TOML, encoding="utf-8")
    return arq


def test_formato_de_validacao_e_detectado_pelo_esperado(sintetico):
    assert av.tem_rotulos_de_validacao(sintetico)
    assert not av.tem_rotulos_de_validacao(EXP / "catalogo.toml")      # o catalogo de desenvolvimento nao tem as listas
    assert len(av.carregar_validacao(sintetico)) == 9


def test_pontuar_categorias():
    c = av.ConsultaVal("X", "", "", frozenset({"AP1"}), frozenset({"AP4"}), frozenset({"AP2", "AP3"}), "AP1")
    p = av.pontuar
    assert p(c, {"AP1"})["resultado"] == "acerto"
    assert p(c, {"AP1", "AP4"}) == {"resultado": "acerto", "omissoes": [], "falsos_positivos": [], "proibidos_violados": [], "toleradas": ["AP4"]}
    r = p(c, {"AP1", "AP3"})
    assert r["resultado"] == "acerto_com_falso_positivo" and r["falsos_positivos"] == ["AP3"] and r["proibidos_violados"] == ["AP3"]
    assert p(c, set())["resultado"] == "omissao" and p(c, set())["omissoes"] == ["AP1"]
    assert p(c, {"AP2"})["resultado"] == "omissao_e_falso_positivo"
    # classe fora de esperado+tolerado e fora de proibido tambem e falso positivo (so nao e "proibido violado")
    c2 = av.ConsultaVal("Y", "", "", frozenset({"AP1"}), frozenset(), frozenset(), "AP1")
    r2 = p(c2, {"AP1", "AP4"})
    assert r2["falsos_positivos"] == ["AP4"] and r2["proibidos_violados"] == []
    # esperado vazio = consulta limpa
    limpa = av.ConsultaVal("Z", "", "", frozenset(), frozenset({"AP4"}), frozenset({"AP3"}), "NENHUM")
    assert p(limpa, set())["resultado"] == "limpa_correta" and p(limpa, {"AP4"})["resultado"] == "limpa_correta"
    assert p(limpa, {"AP3"})["resultado"] == "limpa_com_falso_positivo" and p(limpa, {"AP3"})["proibidos_violados"] == ["AP3"]


def test_alcance_so_afeta_a_omissao():
    c = av.ConsultaVal("X", "", "", frozenset({"AP3", "AP4"}), frozenset(), frozenset(), "AP3")
    assert av.pontuar(c, {"AP3"})["omissoes"] == ["AP4"]                                # estrito
    assert av.pontuar(c, {"AP3"}, alcance=av.SEM_PLANO)["resultado"] == "acerto"       # dentro do alcance do modo so SQL
    assert av.pontuar(c, {"AP1"}, alcance=av.SEM_PLANO)["falsos_positivos"] == ["AP1"]   # falso positivo nao depende do alcance


@pytest.mark.parametrize("trecho, msg", [
    ('esperado = ["AP1"]\ntolerado = []\nproibido = ["AP1"]', "inconsistente"),
    ('esperado = ["AP9"]\ntolerado = []\nproibido = []', "fora de AP1..AP4"),
    ('esperado = []\ntolerado = ["AP4"]\nproibido = ["AP4"]', "inconsistente"),
])
def test_rotulo_invalido_e_recusado(tmp_path, trecho, msg):
    arq = tmp_path / "ruim.toml"
    arq.write_text(f'[meta]\nversao = 1\nbanco = "x"\ndataset = "x"\n[[consulta]]\nid = "R"\nantipadrao = "AP1"\ntitulo = "t"\ntabelas = ["t"]\n'
                   f'seletividade = "alta"\n{trecho}\nsql_original = "SELECT 1"\n', encoding="utf-8")
    with pytest.raises(ValueError, match=msg):
        av.carregar_validacao(arq)


def test_execucao_so_sql_sobre_o_sintetico(sintetico, tmp_path):
    consultas = av.carregar_validacao(sintetico)
    casos, ausentes = av.executar_validacao(consultas, tmp_path / "planos_inexistentes", so_sql=True, lim=av.Limiares())
    assert {k.modo for k in casos} == {"sql"} and ausentes == []
    res = {k.consulta.id: k.pontos for k in casos}
    assert res["T1"]["resultado"] == "acerto"
    assert res["T2"]["resultado"] == "omissao" and res["T2"]["omissoes"] == ["AP4"] and res["T2"]["resultado_no_alcance"] == "acerto"
    assert res["T3"]["resultado"] == "limpa_correta"
    assert res["T4"]["resultado"] == "limpa_com_falso_positivo" and res["T4"]["proibidos_violados"] == ["AP3"]
    assert res["T5"]["resultado"] == "omissao" and res["T5"]["omissoes"] == ["AP2"]
    assert res["T6"]["resultado"] == "acerto_com_falso_positivo" and res["T6"]["falsos_positivos"] == ["AP3"] and res["T6"]["proibidos_violados"] == []
    assert res["T7"]["resultado"] == "acerto" and res["T7"]["toleradas"] == ["AP3"]
    assert res["T8"]["resultado"] == "omissao_e_falso_positivo"
    assert res["T9"]["resultado"] == "omissao"                      # texto nao analisavel nunca vira acerto
    assert not next(k for k in casos if k.consulta.id == "T9").diag.sql_analisavel
    r = av.resumo_validacao(casos, "sql")
    assert (r["consultas"], r["com_esperado"], r["limpas"]) == (9, 7, 2)
    assert (r["acertos"], r["omissoes"], r["acertos_no_alcance"]) == (3, 4, 4)
    assert (r["limpas_corretas"], r["consultas_com_falso_positivo"], r["total_falsos_positivos"]) == (1, 3, 3)
    assert (r["consultas_com_proibido"], r["total_proibidos"]) == (1, 1)


def test_matriz_por_regra_no_sintetico(sintetico, tmp_path):
    casos, _ = av.executar_validacao(av.carregar_validacao(sintetico), tmp_path, so_sql=True, lim=av.Limiares())
    m = {r["regra"]: r for r in av.matriz_regras_validacao(casos, "sql")}
    # T9 (SQL nao analisavel) nao entra: as regras de SQL nao foram executadas nele
    assert (m["S2"]["vp"], m["S2"]["fn"], m["S2"]["fp"]) == (3, 1, 0)       # T1, T6, T7 acertam; T8 nao
    assert (m["S1"]["vp"], m["S1"]["fp"], m["S1"]["fp_proibido"], m["S1"]["toleradas_disparou"], m["S1"]["vn"]) == (1, 3, 1, 1, 3)
    assert (m["S3"]["vp"], m["S3"]["fn"]) == (0, 1)                          # T5: esperado AP2, silenciosa


def test_cli_grava_uma_vez_e_recusa_regravar(sintetico, tmp_path, capsys):
    saida = tmp_path / "out"
    assert av.main(["--catalogo", str(sintetico), "--so-sql", "--saida", str(saida)]) == 0
    md = (saida / "heldout_so_sql_diagnostico.md").read_text(encoding="utf-8")
    assert "PENDENTE" in md and "Wilson" in md and "n pequeno" in md and "T9" in md
    assert "não aplicável" in md                                       # arquivo sintetico nao esta no pre-registro
    with (saida / "heldout_so_sql_casos.csv").open(encoding="utf-8") as f:
        linhas = {r["consulta"]: r for r in csv.DictReader(f)}
    assert linhas["T2"]["resultado"] == "omissao" and linhas["T2"]["omissoes"] == "AP4" and linhas["T2"]["resultado_no_alcance"] == "acerto"
    assert linhas["T4"]["proibidos_violados"] == "AP3"
    assert {"heldout_so_sql_achados.csv", "heldout_so_sql_regras.csv", "heldout_so_sql_resumo.csv"} <= {p.name for p in saida.iterdir()}
    before = (saida / "heldout_so_sql_diagnostico.md").read_text(encoding="utf-8")
    assert av.main(["--catalogo", str(sintetico), "--so-sql", "--saida", str(saida)]) == 2     # nao regrava: roda uma vez
    assert (saida / "heldout_so_sql_diagnostico.md").read_text(encoding="utf-8") == before
    assert av.main(["--catalogo", str(sintetico), "--so-sql", "--saida", str(saida), "--execucao", "apos_ajuste"]) == 0
    assert (saida / "heldout_apos_ajuste_diagnostico.md").exists()


def test_cli_sem_planos_marca_modos_com_plano_como_pendentes(sintetico, tmp_path):
    saida = tmp_path / "out"
    assert av.main(["--catalogo", str(sintetico), "--planos", str(tmp_path / "nao_existe"), "--saida", str(saida)]) == 0
    md = (saida / "heldout_com_planos_diagnostico.md").read_text(encoding="utf-8")
    assert md.count("PENDENTE") == 2 and "SQL + plano real" in md


def test_limiares_diferentes_do_padrao_marcam_apos_ajuste(sintetico, tmp_path):
    saida = tmp_path / "out"
    assert av.main(["--catalogo", str(sintetico), "--so-sql", "--saida", str(saida), "--limiar", "p1_linhas_minimas=5"]) == 0
    md = (saida / "heldout_so_sql_diagnostico.md").read_text(encoding="utf-8")
    assert "após ajuste" in md and "p1_linhas_minimas=5" in md


def test_sha_do_pre_registro_tem_formato_valido():
    h = av.sha_do_pre_registro("nucleo/src/tcclab/diagnostico.py")
    assert h is None or (len(h) == 64 and set(h) <= set("0123456789abcdef"))
    assert av.sha_do_pre_registro("arquivo/que/nao/existe") is None


def test_modo_de_desenvolvimento_continua_igual(tmp_path):
    """O catalogo de desenvolvimento segue pelo caminho antigo (nao pelo da validacao)."""
    assert av.main(["--so-sql", "--saida", str(tmp_path)]) == 0
    assert (tmp_path / "avaliacao_diagnostico.md").exists() and not list(tmp_path.glob("heldout_*"))
