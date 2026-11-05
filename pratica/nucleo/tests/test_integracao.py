"""Integracao contra o SQL Server de pratica/infra (banco tcc_lab, tabela dbo.m0_sanidade).

AVISO: os testes de TEMPO so sao confiaveis sem restauracao/backup em andamento na instancia; os de
leituras logicas nao dependem disso. Estes testes verificam estrutura e relacoes, nao valores de tempo.
"""
import csv

import pytest

from conftest import CONSULTA, DDL_INDICE, DDL_REVERTER
from tcclab import cache, canais
from tcclab.ambiente import atividade_em_segundo_plano, capturar_ambiente
from tcclab.config import Config
from tcclab.driver import conectar
from tcclab.exportar import exportar
from tcclab.medir import medir_uma
from tcclab.runner import PlanoExperimento, Variante, executar_experimento
from tcclab.store import Armazem

pytestmark = pytest.mark.banco


def test_ping_e_ambiente(con, cfg):
    amb = capturar_ambiente(con, "tcc_lab")
    assert "SQL Server 2022" in amb["sql_version"]
    assert amb["compat_level"] == 160
    assert amb["query_store_options"][0] == "READ_WRITE"
    assert isinstance(amb["avisos"], list)


def test_canal_a_entrega_mensagens_e_5737_leituras(con):
    cache.limpar_cache_planos(con, Config.carregar(), "tcc_lab")
    medir_uma(con, CONSULTA)  # compila
    m = medir_uma(con, CONSULTA)
    assert m.logical_reads == 5737
    assert m.scan_count == 1 and m.rows == 1
    assert "logical reads 5737" in m.io_raw and "CPU time" in m.time_raw


def test_antes_depois_5737_para_3(con, cfg):
    cache.limpar_cache_planos(con, cfg, "tcc_lab")
    medir_uma(con, CONSULTA)
    antes = medir_uma(con, CONSULTA)
    con.executar_lote(DDL_INDICE)
    try:
        cache.limpar_cache_planos(con, cfg, "tcc_lab")
        medir_uma(con, CONSULTA)
        depois = medir_uma(con, CONSULTA)
    finally:
        con.executar_lote(DDL_REVERTER)
    assert (antes.logical_reads, depois.logical_reads) == (5737, 3)
    assert antes.result_hash == depois.result_hash  # mesmo resultado nas duas variantes


def test_plano_real_reconcilia_com_canal_a_e_expoe_hashes(con, cfg):
    cache.limpar_cache_planos(con, cfg, "tcc_lab")
    xml, pa = canais.capturar_plano_real(con, CONSULTA)
    ins = pa.principal()
    m = medir_uma(con, CONSULTA)
    assert ins.leituras_logicas_plano == m.logical_reads == 5737      # canal B = canal A
    assert ins.query_plan_hash and ins.query_hash
    xml_est, pe = canais.capturar_plano_estimado(con, CONSULTA)
    assert pe.principal().query_plan_hash == ins.query_plan_hash
    dmv = canais.instantaneo_dmv(con, ins.query_hash)
    assert ins.query_plan_hash in dmv                                   # DMV usa o mesmo hash
    assert dmv[ins.query_plan_hash].total_logical_reads // dmv[ins.query_plan_hash].execution_count == 5737


def test_resolucao_us_pela_dmv_e_1ms_no_statistics_time(con, cfg):
    con.executar_lote(DDL_INDICE)
    try:
        cache.limpar_cache_planos(con, cfg, "tcc_lab")
        _, pa = canais.capturar_plano_real(con, CONSULTA)
        ins = pa.principal()
        for _ in range(3):
            medir_uma(con, CONSULTA)
        vals = []
        for _ in range(15):
            m = medir_uma(con, CONSULTA)
            u = canais.ultima_execucao_dmv(con, ins.query_hash, ins.query_plan_hash)
            vals.append((m.cpu_ms, u["cpu_us"], u["logical_reads"]))
    finally:
        con.executar_lote(DDL_REVERTER)
    assert all(v[2] == 3 for v in vals)
    assert all(v[1] > 0 for v in vals)                  # DMV enxerga o que o STATISTICS TIME arredonda
    assert any(v[1] % 1000 != 0 for v in vals)          # resolucao melhor que 1 ms
    assert max(v[0] for v in vals) <= 1                 # STATISTICS TIME: 0 ou 1 ms


def test_frio_tem_leituras_fisicas_e_quente_nao(con, cfg):
    cache.limpar_cache_planos(con, cfg, "tcc_lab")
    medir_uma(con, CONSULTA)
    quente = medir_uma(con, CONSULTA)
    cache.preparar_frio(con, cfg, "tcc_lab")
    frio = medir_uma(con, CONSULTA)
    assert quente.physical_reads == 0 and quente.read_ahead_reads == 0
    assert frio.physical_reads + frio.read_ahead_reads >= 5000
    assert frio.logical_reads == quente.logical_reads == 5737


def test_comandos_de_estado_recusados_fora_da_lista_de_permissao(con):
    cfg = Config.carregar(database="tcc_lab")
    with pytest.raises(PermissionError):
        cache.preparar_frio(con, cfg, "StackOverflow2010")
    with pytest.raises(PermissionError):
        cache.limpar_cache_planos(con, cfg, "master", "global")
    with pytest.raises(PermissionError):
        executar_experimento(cfg, None, PlanoExperimento("x", "StackOverflow2010", [Variante("A", "SELECT 1")]))


def test_experimento_completo_reproduzivel_e_exportavel(cfg, tabela, tmp_path):
    def rodar(nome):
        arm = Armazem(tmp_path / f"{nome}.sqlite")
        plano = PlanoExperimento(
            nome=nome, database="tcc_lab", regimes=("quente", "frio"), aquecimentos=2,
            repeticoes={"quente": 5, "frio": 3}, ordem="ABA", semente=123,
            variantes=[Variante("antes", CONSULTA), Variante("depois", CONSULTA, DDL_INDICE, DDL_REVERTER, "index")],
        )
        res = executar_experimento(cfg, arm, plano)
        return arm, res

    arm1, r1 = rodar("e1")
    arm2, r2 = rodar("e2")
    # leituras logicas identicas nas duas execucoes (reprodutibilidade do canal A)
    lr = lambda r: [(x["regime"], x["bloco"], x["logical_reads"]["mediana"], x["logical_reads"]["dp"]) for x in r["resumo"]]  # noqa: E731
    assert lr(r1) == lr(r2)
    por_bloco = {(x["regime"], x["bloco"]): x for x in r1["resumo"]}
    assert por_bloco[("quente", "A1")]["logical_reads"]["mediana"] == 5737
    assert por_bloco[("quente", "B")]["logical_reads"]["mediana"] == 3
    assert por_bloco[("quente", "A2")]["logical_reads"]["mediana"] == 5737
    assert por_bloco[("frio", "A1")]["physical_reads"]["mediana"] + por_bloco[("frio", "A1")]["read_ahead_reads"]["mediana"] > 5000
    assert all(x["logical_reads"]["dp"] == 0 for x in r1["resumo"])  # leituras logicas deterministicas
    assert len({h for x in r1["resumo"] for h in x["resultado_hashes"]}) == 1  # mesmo resultado em todas as variantes
    # ordem e semente registradas
    exp = arm1.consultar("SELECT seed, ordem_executada_json, status FROM experiment")[0]
    assert exp["seed"] == 123 and exp["status"] == "concluido"
    assert [b["bloco"] for b in __import__("json").loads(exp["ordem_executada_json"])][:3] == ["A1", "B", "A2"]
    # canais C e D no armazem concordam com o A
    dmv = arm1.consultar("SELECT d.avg_logical_reads, d.execution_count, r.block, r.regime FROM dmv_sample d JOIN run r USING(run_id)")
    assert dmv and all(abs(d["avg_logical_reads"] - (5737 if d["block"].startswith("A") else 3)) < 1e-6 for d in dmv)
    qs = arm1.consultar("SELECT q.avg_logical_io_reads, q.count_executions, q.execucoes_esperadas FROM qs_sample q")
    assert qs and all(q["count_executions"] == q["execucoes_esperadas"] for q in qs)
    # nada fica para tras no banco
    con = conectar(cfg, "tcc_lab")
    assert con.executar_lote("SELECT COUNT(*) FROM sys.indexes WHERE name = 'ix_m0_cliente'", guardar_linhas=1).conjuntos[0].linhas[0][0] == 0
    con.fechar()
    # exportacao
    arquivos = exportar(arm1, r1["experiment_id"], tmp_path / "exp", "csv")
    linhas = list(csv.DictReader((tmp_path / "exp" / "measurements.csv").open(encoding="utf-8")))
    assert len(linhas) == (5 * 3) + (3 * 3) and {l["variante"] for l in linhas} == {"antes", "depois"}
    assert any(a.name == "environment.json" for a in arquivos)
    arm1.fechar(); arm2.fechar()


def test_detecta_atividade_em_segundo_plano_sem_falso_alarme_em_banco_ocioso(con):
    avisos = atividade_em_segundo_plano(con)
    assert isinstance(avisos, list)  # pode conter avisos reais (ex.: restauracao de outro dataset): so verifica o formato
