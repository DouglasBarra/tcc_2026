"""Repeticoes, regimes (quente/frio), ordem dos blocos (ABA) e registro no SQLite.

Fluxo de um bloco (variante x regime):
  1. aplica o DDL da variante (se houver) e limpa o cache de planos do banco;
  2. 1a execucao com plano real (STATISTICS XML): compila o plano e fornece QueryHash/QueryPlanHash;
  3. W aquecimentos (gravados com phase=warmup);
  4. instantaneo de sys.dm_exec_query_stats; N repeticoes medidas (canal A); novo instantaneo;
  5. descarrega e le o Query Store (canal C).
No regime frio, cada repeticao medida e precedida de CHECKPOINT + DBCC DROPCLEANBUFFERS.
Semente e ordem executada ficam gravadas em `experiment`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from . import ambiente, cache, canais
from .config import Config
from .driver import Conexao, conectar
from .medir import SESSAO_PADRAO, agora_utc, medir_uma, preparar_sessao
from .stats import resumir
from .store import Armazem

METRICAS = ("logical_reads", "cpu_ms", "elapsed_ms", "physical_reads", "read_ahead_reads", "cpu_us", "elapsed_us")


@dataclass
class Variante:
    nome: str
    sql: str
    ddl_aplicar: str | None = None
    ddl_reverter: str | None = None
    kind: str = "original"
    repeticoes: dict[str, int] | None = None  # substitui plano.repeticoes por regime (bracos muito lentos)
    aquecimentos: int | None = None            # substitui plano.aquecimentos


@dataclass
class PlanoExperimento:
    nome: str
    database: str
    variantes: list[Variante]
    regimes: tuple[str, ...] = ("quente",)
    aquecimentos: int = 3
    repeticoes: dict[str, int] = field(default_factory=lambda: {"quente": 10, "frio": 5})
    ordem: str = "ABA"           # ABA | AB | aleatoria
    semente: int = 20261005
    cache_planos: str = "escopo"  # escopo | global
    estatisticas_tabelas: tuple[str, ...] = ()
    microssegundos: bool = True   # le last_worker_time/last_elapsed_time (us) apos cada repeticao
    consulta_id: str = "Q"
    titulo: str = ""


def ordem_blocos(variantes: list[Variante], ordem: str, semente: int) -> list[tuple[str, int]]:
    idx = list(range(len(variantes)))
    if ordem == "ABA":
        seq = [idx[0], *idx[1:], idx[0]] if len(idx) > 1 else idx
    elif ordem == "AB":
        seq = idx
    elif ordem == "aleatoria":
        seq = idx[:]
        random.Random(semente).shuffle(seq)
    else:
        raise ValueError(f"ordem desconhecida: {ordem}")
    contagem: dict[int, int] = {}
    rotulos = []
    for i in seq:
        contagem[i] = contagem.get(i, 0) + 1
        letra = chr(ord("A") + i)
        total = seq.count(i)
        rotulos.append((f"{letra}{contagem[i]}" if total > 1 else letra, i))
    return rotulos


class _Estado:
    def __init__(self):
        self.aplicada: Variante | None = None


def _transicionar(con: Conexao, est: _Estado, v: Variante) -> None:
    if est.aplicada is v:
        return
    if est.aplicada is not None and est.aplicada.ddl_reverter:
        con.executar_lote(est.aplicada.ddl_reverter)
    est.aplicada = None
    if v.ddl_aplicar:
        con.executar_lote(v.ddl_aplicar)
        est.aplicada = v


def executar_experimento(
    cfg: Config,
    armazem: Armazem,
    plano: PlanoExperimento,
    progresso: Callable[[str], None] | None = None,
    con: Conexao | None = None,
) -> dict:
    log = progresso or (lambda _m: None)
    cfg.exigir_banco_permitido(plano.database)  # o experimento pode executar DDL e DBCC
    proprio = con is None
    con = con or conectar(cfg, plano.database)
    est = _Estado()
    try:
        preparar_sessao(con)
        amb = ambiente.capturar_ambiente(con, plano.database)
        env_id = armazem.novo_ambiente(amb)
        for aviso in amb["avisos"]:
            log(f"AVISO: {aviso}")
        blocos = ordem_blocos(plano.variantes, plano.ordem, plano.semente)
        manifesto = {
            "nome": plano.nome, "database": plano.database, "regimes": list(plano.regimes),
            "aquecimentos": plano.aquecimentos, "repeticoes": plano.repeticoes, "ordem": plano.ordem,
            "semente": plano.semente, "cache_planos": plano.cache_planos,
            "estatisticas_tabelas": list(plano.estatisticas_tabelas), "microssegundos": plano.microssegundos, "sessao": list(SESSAO_PADRAO),
            "variantes": [vars(v) for v in plano.variantes],
            "blocos_planejados": [b for b, _ in blocos],
        }
        exp_id = armazem.novo_experimento(plano.nome, manifesto, env_id, plano.semente, agora_utc())
        armazem.garantir_consulta(plano.consulta_id, plano.titulo or plano.nome, plano.variantes[0].sql)
        var_ids = [
            armazem.nova_variante(plano.consulta_id, v.nome, v.kind, v.sql, v.ddl_aplicar, v.ddl_reverter)
            for v in plano.variantes
        ]
        executada: list[dict] = []
        seq = 0
        status = "concluido"
        try:
            for regime in plano.regimes:
                for rotulo, i in blocos:
                    seq += 1
                    log(f"[{regime}] bloco {rotulo} ({plano.variantes[i].nome})")
                    run_id = _executar_bloco(con, cfg, armazem, plano, exp_id, var_ids[i], plano.variantes[i], est, seq, regime, rotulo, log)
                    executada.append({"seq": seq, "regime": regime, "bloco": rotulo, "variante": plano.variantes[i].nome, "run_id": run_id})
        except BaseException:
            status = "erro"
            raise
        finally:
            try:
                if est.aplicada is not None and est.aplicada.ddl_reverter:
                    con.executar_lote(est.aplicada.ddl_reverter)  # nunca deixar o indice de teste para tras
            finally:
                armazem.fechar_experimento(exp_id, status, agora_utc(), executada)
        return {"experiment_id": exp_id, "env_id": env_id, "avisos": amb["avisos"], "resumo": resumo_experimento(armazem, exp_id)}
    finally:
        if proprio:
            con.fechar()


def _executar_bloco(con, cfg, armazem, plano, exp_id, variant_id, v, est, seq, regime, rotulo, log) -> int:
    run_id = armazem.novo_run(exp_id, variant_id, seq, regime, rotulo, agora_utc())
    avisos_run: list[str] = []
    try:
        _transicionar(con, est, v)
        for t in plano.estatisticas_tabelas:
            con.executar_lote(f"UPDATE STATISTICS {t} WITH FULLSCAN")
        cache.limpar_cache_planos(con, cfg, plano.database, plano.cache_planos)
        avisos_run += ambiente.atividade_em_segundo_plano(con)
        n = (v.repeticoes or {}).get(regime, plano.repeticoes[regime])
        aquec = v.aquecimentos if v.aquecimentos is not None else plano.aquecimentos

        # 1a execucao: plano real (tambem compila e aquece)
        xml, pa = canais.capturar_plano_real(con, v.sql)
        ins = pa.principal()
        qh = ins.query_hash if ins else None
        qph = ins.query_plan_hash if ins else None
        pid = armazem.gravar_plano(xml, "real", ins)
        armazem.marcar_plano_no_run(run_id, pid, qh, qph)

        cache.aquecer(con, v.sql, aquec)
        qs_antes = canais.instantaneo_qs(con, qh) if qh else {}  # depois do plano real e do aquecimento
        antes = canais.instantaneo_dmv(con, qh) if qh else {}
        for rep in range(1, n + 1):
            if regime == "frio":
                cache.preparar_frio(con, cfg, plano.database)
            m = medir_uma(con, v.sql)
            if qh and plano.microssegundos:
                u = canais.ultima_execucao_dmv(con, qh, qph)
                if u:
                    m.cpu_us, m.elapsed_us, m.dmv_logical_reads = u["cpu_us"], u["elapsed_us"], u["logical_reads"]
            armazem.gravar_medicao(run_id, rep, "measured", m)
        depois = canais.instantaneo_dmv(con, qh) if qh else {}

        for h, d in canais.delta_dmv(antes, depois).items():
            armazem.gravar_dmv(run_id, d)
        if len(canais.delta_dmv(antes, depois)) > 1:
            avisos_run.append("mais de um plano (QueryPlanHash) executou durante o bloco")

        if qh:
            for a in canais.delta_qs(qs_antes, canais.instantaneo_qs(con, qh)):
                armazem.gravar_qs(run_id, a, n)
        armazem.fechar_run(run_id, "concluido", agora_utc(), avisos_run)
        return run_id
    except BaseException:
        armazem.fechar_run(run_id, "erro", agora_utc(), avisos_run)
        raise


def resumo_experimento(armazem: Armazem, exp_id: int) -> list[dict]:
    """Estatistica descritiva por run (variante x regime x bloco), so repeticoes medidas."""
    saida = []
    runs = armazem.consultar(
        "SELECT r.run_id, r.regime, r.block, r.query_plan_hash, v.nome AS variante FROM run r "
        "JOIN variant v ON v.variant_id = r.variant_id WHERE r.experiment_id = ? ORDER BY r.seq",
        (exp_id,),
    )
    for r in runs:
        linhas = armazem.consultar(
            "SELECT * FROM measurement WHERE run_id = ? AND phase = 'measured' ORDER BY rep", (r["run_id"],)
        )
        if not linhas:
            continue
        item = {"run_id": r["run_id"], "regime": r["regime"], "bloco": r["block"], "variante": r["variante"],
                "query_plan_hash": r["query_plan_hash"], "n": len(linhas),
                "resultado_hashes": sorted({l["result_hash"] for l in linhas if l["result_hash"]}),
                "linhas_resultado": sorted({l["rows"] for l in linhas})}
        for m in METRICAS:
            vals = [float(l[m]) for l in linhas if l[m] is not None]
            if vals:
                item[m] = resumir(vals).como_dict()
        saida.append(item)
    return saida
