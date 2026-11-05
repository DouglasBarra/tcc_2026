"""Validacao (antes x depois), custo de escrita e estados vazios. Dados SINTETICOS (ver conftest)."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from conftest import criar_rodada_sintetica
from tcclab_api.adaptadores import AdaptadorDiagnostico, GerenteAnalise, importar_analise
from tcclab_api.main import create_app

analise_ok = importar_analise()[0] is not None
precisa_analise = pytest.mark.skipif(not analise_ok, reason="tcclab.analise (numpy/scipy) indisponível neste ambiente")


def _g(v, regime, metrica, variante):
    return next(g for g in v["grupos"] if g["regime"] == regime and g["metrica"] == metrica and g["variante"] == variante)


@precisa_analise
def test_validacao_traz_razao_ic_e_origem_por_run_id(cliente):
    v = cliente.get("/api/rodadas/sint-1/consultas/T1/validacao").json()
    assert v["disponivel"] and v["analise"]["disponivel"] and v["tratamento_padrao"] == "reescrita+indice"
    c = next(c for c in v["comparacoes"] if c["regime"] == "quente" and c["metrica"] == "logical_reads" and c["tratamento"] == "reescrita+indice")
    assert c["mediana_antes"] == 1000 and c["mediana_depois"] == 50 and c["razao"] == pytest.approx(0.05)
    assert c["reducao_pct"] == pytest.approx(95.0) and c["ic_lo"] == c["ic_hi"] == pytest.approx(0.05)   # deterministico: IC degenerado
    # rastreio: run_ids do antes sao os dois blocos do original (A1 e A2) e os do depois, o bloco D
    assert len(c["run_ids_antes"]) == 2 and len(c["run_ids_depois"]) == 1
    g = _g(v, "quente", "logical_reads", "original")
    assert g["n"] == 16 and g["fonte"] == "tcclab.analise" and g["run_ids"] == c["run_ids_antes"]


@precisa_analise
def test_regimes_quente_e_frio_sao_separados(cliente):
    v = cliente.get("/api/rodadas/sint-1/consultas/T1/validacao").json()
    assert {g["regime"] for g in v["grupos"]} == {"quente", "frio"}
    # leituras fisicas: 0 no quente e 7 no frio (sintetico)
    assert _g(v, "quente", "physical_reads", "original")["mediana"] == 0
    assert _g(v, "frio", "physical_reads", "original")["mediana"] == 7
    assert set(_g(v, "quente", "logical_reads", "original")["run_ids"]).isdisjoint(_g(v, "frio", "logical_reads", "original")["run_ids"])


@precisa_analise
def test_veredictos_usam_criterio_do_modulo_e_nao_do_frontend(cliente):
    v = cliente.get("/api/rodadas/sint-1/consultas/T1/validacao").json()
    assert v["analise"]["criterios"]["reducao_minima_pct"] > 0 and v["analise"]["criterios_sha"]
    ver = next(x for x in v["veredictos"] if x["regime"] == "quente" and x["tratamento"] == "reescrita+indice")
    assert ver["reducao_ok"] is True and ver["ic_exclui_1"] is True


def test_sem_modulo_de_analise_mostra_descritiva_e_motivo(tmp_cfg, rodada):
    sem = GerenteAnalise(None, "módulo tcclab.analise ainda não disponível")
    c = TestClient(create_app(tmp_cfg, gerente=sem))
    v = c.get("/api/rodadas/sint-1/consultas/T1/validacao").json()
    assert v["analise"] == {"disponivel": False, "motivo": "módulo tcclab.analise ainda não disponível", "criterios": None, "criterios_sha": None, "meta": None}
    assert v["comparacoes"] == [] and v["veredictos"] == []        # nada de razao/IC inventados
    g = _g(v, "quente", "logical_reads", "reescrita+indice")
    assert g["mediana"] == 50 and g["fonte"] == "tcclab.stats" and g["ic_lo"] is None
    assert all(d["deterministico"] for d in v["determinismo"])
    e = c.get("/api/rodadas/sint-1/consultas/T1/escrita").json()
    assert e["disponivel"] and e["ratio_disponivel"] is False
    assert e["operacoes"][0]["metricas"]["logical_reads"]["razao"] is None
    assert e["operacoes"][0]["metricas"]["logical_reads"]["sem_indice"]["mediana"] > 0


def test_consulta_do_catalogo_sem_medicao_e_estado_vazio(cliente):
    v = cliente.get("/api/rodadas/sint-1/consultas/T3/validacao").json()
    assert v["disponivel"] is False and "não tem experimento" in v["motivo"] and v["experimentos"] == []
    e = cliente.get("/api/rodadas/sint-1/consultas/T3/escrita").json()
    assert e["disponivel"] is False and "não há medição" in e["motivo"]
    r = cliente.get("/api/rodadas/sint-1/consultas/T3/repeticoes", params={"metrica": "logical_reads"}).json()
    assert r["disponivel"] is False and r["pontos"] == []


def test_experimento_descartado_por_deriva_nao_e_o_padrao(tmp_cfg):
    criar_rodada_sintetica(tmp_cfg.resultados, "deriva", segundo_experimento_descartado=True)
    c = TestClient(create_app(tmp_cfg))
    v = c.get("/api/rodadas/deriva/consultas/T1/validacao").json()
    assert v["experimento"]["experiment_id"] == 2
    d = c.get("/api/rodadas/deriva/consultas/T1/validacao", params={"experimento": 1}).json()
    assert d["experimento"]["descartado_por_deriva"] is True
    assert any("DESCARTADO" in a for a in d["avisos"])
    assert c.get("/api/rodadas/deriva/consultas/T1/validacao", params={"experimento": 77}).json()["disponivel"] is False


def test_dados_parciais_sao_avisados(tmp_cfg):
    criar_rodada_sintetica(tmp_cfg.resultados, "viva", status="executando", mtime_antigo=False)
    v = TestClient(create_app(tmp_cfg)).get("/api/rodadas/viva/consultas/T1/validacao").json()
    assert any("parciais" in a for a in v["avisos"])


def test_equivalencia_e_nativo_vem_da_coleta(cliente):
    v = cliente.get("/api/rodadas/sint-1/consultas/T1/validacao").json()
    assert v["coleta"]["equivalencia"]["ok"] is True and v["coleta"]["nativo_ddl"].startswith("CREATE INDEX")
    assert v["coleta"]["deriva_a1_a2"] == pytest.approx(0.05)


def test_repeticoes_para_grafico_de_pontos(cliente):
    r = cliente.get("/api/rodadas/sint-1/consultas/T1/repeticoes", params={"metrica": "logical_reads"}).json()
    assert r["disponivel"] and len(r["pontos"]) == 5 * 2 * 8
    assert {"run_id", "regime", "variante", "rep", "valor"} <= set(r["pontos"][0])
    assert cliente.get("/api/rodadas/sint-1/consultas/T1/repeticoes", params={"metrica": "rows; drop"}).status_code == 422


@precisa_analise
def test_escrita_razao_ic_e_origem(cliente):
    e = cliente.get("/api/rodadas/sint-1/consultas/T1/escrita").json()
    assert e["disponivel"] and e["indice"]["nome"] == "ix_t1" and e["indice"]["paginas"] == 40
    ins = next(o for o in e["operacoes"] if o["operacao"] == "insert")["metricas"]["logical_reads"]
    assert ins["sem_indice"]["n"] == 5 and ins["com_indice"]["n"] == 5
    assert ins["razao"] == pytest.approx(110 / 50, rel=0.05) and ins["ic_lo"] is not None
    assert len(ins["linhas_sem_indice"]) == 5 and set(ins["linhas_sem_indice"]).isdisjoint(ins["linhas_com_indice"])
    assert "limiar" not in str(e).lower()      # o pre-registro nao define limiar para escrita: a API nao inventa um


@precisa_analise
def test_analise_agregada_roda_em_segundo_plano_e_exclui_controles(tmp_cfg):
    import time
    criar_rodada_sintetica(tmp_cfg.resultados, "agg")
    c = TestClient(create_app(tmp_cfg))
    assert c.get("/api/rodadas/agg/analise").json()["estado"] == "nao_calculada"      # ler a tela nao dispara bootstrap
    for _ in range(100):
        r = c.get("/api/rodadas/agg/analise", params={"calcular": "true"}).json()
        if r["estado"] != "calculando":
            break
        time.sleep(0.1)
    assert r["estado"] == "pronto" and r["experimentos_analisados"] == [1]
    ag = r["resultado"]["agregados"]
    assert ag and all("p_bruto" in a and "n_consultas" in a for a in ag)
    assert r["resultado"]["criterios"]["alfa"] == 0.05


def test_analise_agregada_sem_experimento_concluido(tmp_cfg):
    criar_rodada_sintetica(tmp_cfg.resultados, "vazia", status="executando")
    r = TestClient(create_app(tmp_cfg)).get("/api/rodadas/vazia/analise").json()
    assert r["estado"] == "indisponivel" and "nenhum experimento concluído" in r["motivo"]


def test_registro_da_coleta_mostra_descarte_invalidacao_e_nativo(tmp_cfg):
    criar_rodada_sintetica(tmp_cfg.resultados, "reg", segundo_experimento_descartado=True)
    c = TestClient(create_app(tmp_cfg))
    v = c.get("/api/rodadas/reg/consultas/T1/validacao").json()
    reg = v["registro_coleta"]
    assert [(r["tentativa"], r["descartado_por_deriva"]) for r in reg] == [(1, True), (2, False)]
    assert reg[0]["deriva_a1_a2"] == pytest.approx(0.05) and reg[1]["nativo_ddl"].startswith("CREATE INDEX")
    # consulta invalidada pela equivalencia: sem experimento, mas o motivo aparece e nada e medido
    t2 = c.get("/api/rodadas/reg/consultas/T2/validacao").json()
    assert t2["disponivel"] is False and "invalida_equivalencia" in t2["motivo"]
    assert t2["registro_coleta"][0]["equivalencia"]["ok"] is False and t2["sem_experimento"][0]["status"] == "invalida_equivalencia"


@precisa_analise
def test_escrita_da_api_concorda_com_a_do_modulo_de_analise(tmp_cfg, rodada):
    """A API nao pode divergir do que o artigo vai reportar: medianas e razoes de escrita = as de tcclab.analise."""
    mod, _ = importar_analise()
    if "escrita" not in mod.Analise.__dataclass_fields__:
        pytest.skip("esta versao de tcclab.analise ainda nao calcula o custo de escrita")
    ref = mod.analisar(rodada / "tcclab.sqlite", [1])
    mod_rows = {(r["operacao"], r["metrica"]): r for r in ref.escrita}
    api = TestClient(create_app(tmp_cfg)).get("/api/rodadas/sint-1/consultas/T1/escrita").json()
    comparados = 0
    for op in api["operacoes"]:
        for metrica, m in op["metricas"].items():
            r = mod_rows.get((op["operacao"], metrica))
            if r is None:
                continue
            assert m["sem_indice"]["mediana"] == pytest.approx(r["mediana_sem"]) and m["com_indice"]["mediana"] == pytest.approx(r["mediana_com"])
            assert (m["razao"] if m["razao"] is not None else m["razao_reg"]) == pytest.approx(r["razao"] if r["razao"] is not None else r["razao_reg"])
            comparados += 1
    assert comparados >= 6
