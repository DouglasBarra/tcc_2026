"""Analise estatistica (relatorio 16, secao 6.4) sobre dados JA gravados no SQLite.

Nunca abre o SQL Server: le o arquivo SQLite em modo somente leitura (mode=ro) e calcula tudo em memoria.
Exige o extra opcional `analise` (numpy e scipy).

Unidades: a repeticao mede o ruido de UMA consulta; a CONSULTA e a unidade de amostragem entre consultas.
Misturar os dois niveis inflaria o n, por isso o Wilcoxon e o bootstrap entre consultas usam UM numero por
consulta (a razao das medianas), nunca as repeticoes.

Regra de medianas iguais a zero (CPU em ms costuma dar 0): a razao depois/antes nao existe com antes = 0.
  - ambas_zero : razao 1 (sem mudanca detectavel na resolucao da medida);
  - depois_zero: razao bruta 0 (reducao de 100%); para o log, o IC e os testes o zero e trocado pelo PISO
                 (meia unidade da resolucao, `piso_zero`, 0,5), logo a razao regularizada e piso/antes, uma cota
                 SUPERIOR da razao verdadeira (conservador para afirmar melhoria);
  - antes_zero : razao bruta indefinida (None); regularizada depois/piso, cota INFERIOR (conservador para piora).
O piso vale tambem dentro de cada reamostra do bootstrap. Medianas nao nulas nunca sao alteradas.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
import sqlite3
import warnings
import zlib
from collections import Counter
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy
from scipy import stats as sps

from . import __version__
from .stats import resumir
from .store import VERSAO_ESQUEMA

VERSAO_ANALISE = "1"
FATOR_TUKEY = 1.5          # o mesmo de stats.resumir (pre-registro 6.4); testado contra ele
N_MAX_PERMUTACAO = 14      # com empates/zeros e n_efetivo <= 14: enumeracao exata dos 2^n sinais
N_MAX_EXATO = 50           # limite de `method='auto'` do scipy para o Wilcoxon sem empates
SEM_ANTIPADRAO = "(sem antipadrao)"
PRESCRICAO = "prescricao_do_framework"   # grupo do tratamento principal (prescricao, ou indice quando nao ha reescrita)
METRICAS_ESCRITA = ("logical_reads", "log_bytes", "cpu_ms", "elapsed_ms")   # colunas de escrita_medicao
PAPEIS = ("principal", "controle")   # papel da consulta; vem de catalog_query.notes (convencao de experimento/coletar.py)
METRICAS_PADRAO = ("logical_reads", "physical_reads", "read_ahead_reads", "cpu_ms", "elapsed_ms", "cpu_us", "elapsed_us")
ALTERNATIVAS = ("two-sided", "less", "greater")


# ----------------------------------------------------------------------------------------- criterios
@dataclass(frozen=True)
class Criterios:
    """Todas as decisoes parametrizaveis da analise. Gravadas junto de toda saida (criterios.json)."""

    alfa: float = 0.05
    nivel_ic: float = 0.95
    reamostragens: int = 10000
    semente: int = 20261005
    alternativa: str = "two-sided"          # alternativa do Wilcoxon sobre log(razao); "less" = so reducao
    metricas_teste: tuple[str, ...] = ("logical_reads", "cpu_ms", "elapsed_ms")   # familia do Holm
    metricas_resumo: tuple[str, ...] = METRICAS_PADRAO
    n_min_consultas: int = 6                 # abaixo disso o resultado entre consultas e so descritivo
    metrica_criterio: str = "logical_reads"
    reducao_minima_pct: float = 20.0         # marcador de posicao do relatorio 16 (a decidir)
    degradacao_escrita_max_pct: float | None = None  # None = sem limiar (PRE-REGISTRO.md retirou o 10% do relatorio 16)
    metrica_escrita: str = "logical_reads"   # coluna de escrita_medicao usada no veredito (maximo das razoes por operacao)
    kind_baseline: str = "original"
    kinds_prescricao: tuple[str, ...] = ("prescricao", "indice")   # tratamento principal: o 1o que existir na consulta
    kind_nativo: str = "nativo"              # braco comparado tambem com o tratamento principal
    regime_principal: str = "quente"         # leituras logicas da analise principal; o outro regime sai em tabela propria
    piso_zero: float = 0.5
    limite_deriva_pct: float = 25.0          # |mediana(ultimo bloco da base)/mediana(primeiro) - 1| (25% = PRE-REGISTRO.md)
    papeis_principais: tuple[str, ...] = ("principal",)   # papeis na analise principal; os demais (controle) ficam em separado
    fase: str = "measured"

    def __post_init__(self):
        if not 0 < self.alfa < 1 or not 0 < self.nivel_ic < 1:
            raise ValueError("alfa e nivel_ic devem estar em (0, 1)")
        if self.reamostragens < 100:
            raise ValueError("reamostragens >= 100")
        if self.semente < 0:
            raise ValueError("semente deve ser >= 0")
        if self.alternativa not in ALTERNATIVAS:
            raise ValueError(f"alternativa deve ser uma de {ALTERNATIVAS}")
        if self.piso_zero <= 0:
            raise ValueError("piso_zero deve ser > 0")
        if self.degradacao_escrita_max_pct is not None and self.degradacao_escrita_max_pct < 0:
            raise ValueError("degradacao_escrita_max_pct deve ser >= 0 ou None")
        if self.metrica_escrita not in METRICAS_ESCRITA:
            raise ValueError(f"metrica_escrita deve ser uma de {METRICAS_ESCRITA}")
        if not self.kinds_prescricao:
            raise ValueError("kinds_prescricao vazio")
        if not self.papeis_principais or set(self.papeis_principais) - set(PAPEIS):
            raise ValueError(f"papeis_principais deve ser um subconjunto nao vazio de {PAPEIS}")
        if not self.metricas_teste:
            raise ValueError("metricas_teste vazia")
        faltam = (set(self.metricas_teste) | {self.metrica_criterio}) - set(self.metricas_resumo)
        if faltam:
            raise ValueError(f"metricas fora de metricas_resumo: {sorted(faltam)}")

    @classmethod
    def de_dict(cls, dados: dict) -> "Criterios":
        validos = {f.name for f in fields(cls)}
        desconhecidos = set(dados) - validos
        if desconhecidos:
            raise ValueError(f"criterio(s) desconhecido(s): {sorted(desconhecidos)}; validos: {sorted(validos)}")
        conv = {k: (tuple(v) if isinstance(v, list) else v) for k, v in dados.items()}
        return cls(**conv)

    @classmethod
    def de_arquivo(cls, caminho: str | Path) -> "Criterios":
        return cls.de_dict(json.loads(Path(caminho).read_text(encoding="utf-8")))

    def como_dict(self) -> dict:
        return {f.name: (list(getattr(self, f.name)) if isinstance(getattr(self, f.name), tuple) else getattr(self, f.name))
                for f in fields(self)}

    def sha(self) -> str:
        return hashlib.sha256(json.dumps(self.como_dict(), sort_keys=True).encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------------------- primitivas
def rng_para(semente: int, *chave) -> np.random.Generator:
    """Gerador independente da ordem de processamento: semente fixa + crc32 estavel da chave."""
    h = zlib.crc32("|".join(str(c) for c in chave).encode("utf-8"))
    return np.random.default_rng([int(semente), h])


def _medianas_bootstrap(x: np.ndarray, rng: np.random.Generator, b: int) -> np.ndarray:
    n = len(x)
    saida = np.empty(b)
    lote = max(1, 2_000_000 // n)
    i = 0
    while i < b:
        k = min(lote, b - i)
        saida[i:i + k] = np.median(x[rng.integers(0, n, size=(k, n))], axis=1)
        i += k
    return saida


def _percentis(v: np.ndarray, nivel: float) -> tuple[float, float]:
    cauda = (1 - nivel) / 2 * 100
    lo, hi = np.percentile(v, [cauda, 100 - cauda])
    return float(lo), float(hi)


def _razao_reg(antes, depois, piso: float):
    """Razao depois/antes com zero trocado pelo piso (vale para escalares e vetores)."""
    a = np.where(np.asarray(antes) == 0, piso, antes)
    d = np.where(np.asarray(depois) == 0, piso, depois)
    return d / a


@dataclass(frozen=True)
class Razao:
    bruta: float | None   # depois/antes; None se antes == 0 e depois > 0
    reg: float            # regularizada (usada em log, IC e testes)
    regra: str            # "" | ambas_zero | depois_zero | antes_zero

    @property
    def reducao_pct(self) -> float | None:
        return None if self.bruta is None else (1 - self.bruta) * 100


def razao_medianas(antes: float, depois: float, piso: float = 0.5) -> Razao:
    if antes == 0 and depois == 0:
        return Razao(1.0, 1.0, "ambas_zero")
    if depois == 0:
        return Razao(0.0, float(_razao_reg(antes, depois, piso)), "depois_zero")
    if antes == 0:
        return Razao(None, float(_razao_reg(antes, depois, piso)), "antes_zero")
    r = depois / antes
    return Razao(r, r, "")


def ic_mediana(x: np.ndarray, crit: Criterios, *chave) -> tuple[float, float]:
    """IC percentil por bootstrap da mediana (reamostra as repeticoes)."""
    return _percentis(_medianas_bootstrap(x, rng_para(crit.semente, "med", *chave), crit.reamostragens), crit.nivel_ic)


def ic_razao(antes: np.ndarray, depois: np.ndarray, crit: Criterios, *chave) -> tuple[float, float]:
    """IC percentil por bootstrap da razao depois/antes das medianas (reamostra cada grupo, sem pareamento)."""
    rng = rng_para(crit.semente, "razao", *chave)
    ma = _medianas_bootstrap(antes, rng, crit.reamostragens)
    md = _medianas_bootstrap(depois, rng, crit.reamostragens)
    return _percentis(_razao_reg(ma, md, crit.piso_zero), crit.nivel_ic)


def holm(pvalores: list[float | None], m: int | None = None) -> list[float | None]:
    """Correcao de Holm (passo descendente). `m` = tamanho da familia (padrao: quantos p ha); p None passa direto."""
    idx = [i for i, p in enumerate(pvalores) if p is not None]
    m = m or len(idx)
    saida: list[float | None] = [None] * len(pvalores)
    atual = 0.0
    for k, i in enumerate(sorted(idx, key=lambda j: pvalores[j])):
        atual = max(atual, (m - k) * pvalores[i])
        saida[i] = min(1.0, atual)
    return saida


def p_minimo_wilcoxon(n_efetivo: int, alternativa: str = "two-sided") -> float:
    """Menor p atingivel com n diferencas nao nulas (todas no mesmo sinal): 2/2^n bilateral, 1/2^n unilateral."""
    if n_efetivo <= 0:
        return 1.0
    return min(1.0, (2 if alternativa == "two-sided" else 1) / 2 ** n_efetivo)


def wilcoxon_log_razao(razoes_reg: list[float], crit: Criterios) -> dict:
    """Wilcoxon de postos sinalizados sobre log(razao) (pareado entre consultas; H0: mediana do log = 0).

    Zeros (razao exatamente 1) sao descartados (convencao 'wilcox' do scipy); os logs sao arredondados a 12 casas
    para que razoes iguais virem empates exatos. Metodo do p: exato sem empates (n <= 50), enumeracao dos sinais
    com empates (n_efetivo <= 14), assintotico nos demais casos; o metodo usado fica registrado.
    """
    d = np.round(np.log(np.asarray(razoes_reg, dtype=float)), 12)
    nz = d[d != 0]
    n_ef = int(len(nz))
    saida = {"n": int(len(d)), "n_efetivo": n_ef, "n_zeros": int(len(d) - n_ef),
             "n_reduz": int((d < 0).sum()), "n_aumenta": int((d > 0).sum()),
             "p_minimo": p_minimo_wilcoxon(n_ef, crit.alternativa), "estatistica": None, "p": None, "metodo_p": "sem_dados"}
    if len(d) == 0:
        return saida
    if n_ef == 0:
        return saida | {"p": 1.0, "metodo_p": "sem_diferencas"}
    empates = len(np.unique(np.abs(nz))) < n_ef
    if empates and n_ef <= N_MAX_PERMUTACAO:
        metodo, nome = sps.PermutationMethod(n_resamples=2 ** n_ef, rng=np.random.default_rng(0)), "permutacao_exata"
    elif empates or n_ef > N_MAX_EXATO:
        metodo, nome = "asymptotic", "assintotico" + ("_com_empates" if empates else "")
    else:
        metodo, nome = "exact", "exato"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = sps.wilcoxon(nz, alternative=crit.alternativa, method=metodo)
    return saida | {"estatistica": float(res.statistic), "p": float(res.pvalue), "metodo_p": nome}


def shapiro_p(x: np.ndarray) -> float | None:
    """p de Shapiro-Wilk; None se n < 3 ou dados constantes (indefinido). Serve so para JUSTIFICAR o nao parametrico."""
    if len(x) < 3 or float(np.ptp(x)) == 0.0:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        p = float(sps.shapiro(x).pvalue)
    return None if math.isnan(p) else p


def efeito_entre_consultas(razoes_reg: list[float], crit: Criterios, *chave) -> dict:
    """Reducao mediana (%) = (1 - mediana das razoes) * 100, com IC percentil por bootstrap das CONSULTAS."""
    r = np.asarray(razoes_reg, dtype=float)
    n = len(r)
    saida = {"razao_mediana": float(np.median(r)), "razao_geometrica": float(np.exp(np.mean(np.log(r)))),
             "reducao_mediana_pct": float((1 - np.median(r)) * 100), "reducao_ic_lo": None, "reducao_ic_hi": None,
             "ic_confiavel": False}
    if n >= 2:
        rng = rng_para(crit.semente, "agg", *chave)
        meds = _medianas_bootstrap(r, rng, crit.reamostragens)
        lo, hi = _percentis((1 - meds) * 100, crit.nivel_ic)
        saida |= {"reducao_ic_lo": lo, "reducao_ic_hi": hi, "ic_confiavel": n >= crit.n_min_consultas}
    return saida


# ----------------------------------------------------------------------------------------- dados
@dataclass
class RunDados:
    run_id: int
    seq: int
    bloco: str | None
    query_plan_hash: str | None
    dop: int | None
    com_aviso: bool
    reps: list[int] = field(default_factory=list)
    valores: dict[str, list[float | None]] = field(default_factory=dict)


@dataclass
class Grupo:
    """Medicoes de uma variante em um regime, todos os blocos juntos (consulta x variante x regime)."""
    experiment_id: int
    query_id: str
    variant_id: int
    variante: str
    kind: str
    regime: str
    runs: list[RunDados] = field(default_factory=list)

    @property
    def run_ids(self) -> list[int]:
        return [r.run_id for r in self.runs]

    def pares(self, metrica: str) -> list[tuple[int, int, float]]:
        """(run_id, rep, valor) na ordem de execucao, sem os valores nulos."""
        return [(r.run_id, rep, float(v)) for r in self.runs
                for rep, v in zip(r.reps, r.valores.get(metrica, [])) if v is not None]

    def vetor(self, metrica: str) -> np.ndarray:
        return np.array([p[2] for p in self.pares(metrica)], dtype=float)


@dataclass
class Consulta:
    experiment_id: int
    query_id: str
    rotulo: str
    titulo: str | None
    antipadrao: str
    seletividade: str
    papel: str = "principal"


@dataclass
class Analise:
    criterios: Criterios
    meta: dict
    avisos: list[str]
    consultas: list[dict]
    resumos: list[dict]
    comparacoes: list[dict]
    veredictos: list[dict]
    agregados: list[dict]
    determinismo: list[dict]
    atipicos: list[dict]
    escrita: list[dict] = field(default_factory=list)       # custo de escrita (escrita_medicao): razao com/sem indice, sem limiar
    descartados: list[dict] = field(default_factory=list)   # experimentos excluidos por deriva (coleta_consulta.descartado_por_deriva)
    grupos: dict = field(default_factory=dict, repr=False)   # (exp, query, variant, regime) -> Grupo; nao serializado

    def como_dict(self) -> dict:
        return {"meta": self.meta, "criterios": self.criterios.como_dict(), "criterios_sha": self.criterios.sha(),
                "avisos": self.avisos, "consultas": self.consultas, "resumos": self.resumos,
                "comparacoes": self.comparacoes, "veredictos": self.veredictos, "agregados": self.agregados,
                "determinismo": self.determinismo, "atipicos": self.atipicos, "escrita": self.escrita,
                "descartados": self.descartados}


def _abrir_somente_leitura(caminho: str | Path) -> sqlite3.Connection:
    caminho = Path(caminho).resolve()
    if not caminho.is_file():
        raise FileNotFoundError(f"banco SQLite inexistente: {caminho}")
    con = sqlite3.connect(caminho.as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    v = con.execute("PRAGMA user_version").fetchone()[0]
    if v != VERSAO_ESQUEMA:
        con.close()
        raise RuntimeError(f"{caminho}: esquema v{v}, a analise espera v{VERSAO_ESQUEMA}")
    return con


def _sha_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def _carregar(con: sqlite3.Connection, experimentos: list[int], crit: Criterios, antipadroes: dict[str, str] | None,
              avisos: list[str], papeis: dict[str, str] | None = None):
    colunas = {r["name"] for r in con.execute("PRAGMA table_info(measurement)")}
    ruins = [m for m in crit.metricas_resumo if m not in colunas]
    if ruins:
        raise ValueError(f"metricas inexistentes em measurement: {ruins}")
    ph = ",".join("?" for _ in experimentos)
    exps = {r["experiment_id"]: r for r in con.execute(f"SELECT * FROM experiment WHERE experiment_id IN ({ph})", experimentos)}
    faltam = [e for e in experimentos if e not in exps]
    if faltam:
        raise KeyError(f"experimento(s) inexistente(s): {faltam}")
    for e in experimentos:
        if exps[e]["status"] != "concluido":
            avisos.append(f"experimento {e} com status '{exps[e]['status']}': dados possivelmente parciais")
    envs = {r["env_id"]: r for r in con.execute("SELECT * FROM environment")}
    ambientes = sorted({exps[e]["env_id"] for e in experimentos})
    campos_amb = ("sql_version", "edition", "compat_level", "ce_version", "cpu_count", "physical_memory_mb", "max_server_memory_mb",
                  "maxdop", "container_platform", "database", "driver", "python_lock_hash")
    assinaturas = {tuple(envs[e][c] for c in campos_amb) for e in ambientes if e in envs}   # cada experimento grava o seu env_id
    if len(assinaturas) > 1:
        difs = [c for i, c in enumerate(campos_amb) if len({a[i] for a in assinaturas}) > 1]
        avisos.append(f"experimentos em ambientes DIFERENTES (env_id {ambientes}; campos que diferem: {difs}): "
                      "confira se podem ser agregados")
    for e in ambientes:
        if envs.get(e) and json.loads(envs[e]["avisos_json"] or "[]"):
            avisos.append(f"ambiente {e} registrou atividade em segundo plano: metricas de TEMPO nao sao confiaveis")

    cols = ", ".join(f"m.{m}" for m in crit.metricas_resumo)
    sql = f"""SELECT r.run_id, r.experiment_id, r.seq, r.regime, r.block, r.status, r.query_plan_hash, r.avisos_json,
                     v.variant_id, v.nome, v.kind, v.query_id, c.title, c.antipattern_id, c.selectivity_class, c.notes,
                     p.dop, m.rep, {cols}
              FROM run r JOIN variant v ON v.variant_id = r.variant_id
              LEFT JOIN catalog_query c ON c.query_id = v.query_id
              LEFT JOIN plan p ON p.plan_id = r.plan_id
              JOIN measurement m ON m.run_id = r.run_id
              WHERE r.experiment_id IN ({ph}) AND m.phase = ?
              ORDER BY r.experiment_id, r.seq, m.rep"""
    linhas = list(con.execute(sql, [*experimentos, crit.fase]))
    ignorados = sorted({(l["experiment_id"], l["run_id"]) for l in linhas if l["status"] != "concluido"})
    if ignorados:
        avisos.append(f"runs ignorados por status diferente de 'concluido': {[r for _, r in ignorados]}")
    linhas = [l for l in linhas if l["status"] == "concluido"]
    if not linhas:
        raise ValueError(f"nenhuma medicao (fase '{crit.fase}') nos experimentos {experimentos}")

    contagem = Counter(l["query_id"] for l in {(l["experiment_id"], l["query_id"]): l for l in linhas}.values())
    antipadroes = antipadroes or {}
    papeis = papeis or {}
    ruins = {p for p in papeis.values() if p not in PAPEIS}
    if ruins:
        raise ValueError(f"papel invalido {sorted(ruins)}; validos: {PAPEIS}")
    consultas: dict[tuple[int, str], Consulta] = {}
    grupos: dict[tuple[int, str, int, str], Grupo] = {}
    runs: dict[int, RunDados] = {}
    for l in linhas:
        ck = (l["experiment_id"], l["query_id"])
        if ck not in consultas:
            rot = l["query_id"] if contagem[l["query_id"]] == 1 else f"{l['query_id']} (exp. {l['experiment_id']})"
            consultas[ck] = Consulta(l["experiment_id"], l["query_id"], rot, l["title"],
                                     antipadroes.get(l["query_id"]) or l["antipattern_id"] or SEM_ANTIPADRAO,
                                     l["selectivity_class"] or "",
                                     papeis.get(l["query_id"]) or (l["notes"] if l["notes"] in PAPEIS else "principal"))
        gk = (l["experiment_id"], l["query_id"], l["variant_id"], l["regime"])
        g = grupos.setdefault(gk, Grupo(l["experiment_id"], l["query_id"], l["variant_id"], l["nome"], l["kind"], l["regime"]))
        if l["run_id"] not in runs:
            avisos_run = json.loads(l["avisos_json"] or "[]")
            runs[l["run_id"]] = RunDados(l["run_id"], l["seq"], l["block"], l["query_plan_hash"], l["dop"], bool(avisos_run),
                                         valores={m: [] for m in crit.metricas_resumo})
            g.runs.append(runs[l["run_id"]])
        rd = runs[l["run_id"]]
        rd.reps.append(l["rep"])
        for m in crit.metricas_resumo:
            rd.valores[m].append(l[m])
    dupl = sorted(q for q, n in contagem.items() if n > 1)
    if dupl:
        avisos.append(f"query_id repetido em mais de um experimento ({dupl}): tratados como consultas DISTINTAS "
                      "(replicatas contariam como consultas e inflariam o n entre consultas)")
    com_aviso = sorted(r.run_id for r in runs.values() if r.com_aviso)
    if com_aviso:
        avisos.append(f"runs com aviso de atividade em segundo plano {com_aviso}: metricas de TEMPO nao sao confiaveis "
                      "(leituras logicas nao dependem disso)")
    return consultas, grupos, exps, envs


def _colunas(con: sqlite3.Connection, tabela: str) -> set[str]:
    """Colunas da tabela (vazio se a tabela nao existe): tabelas auxiliares do coletor podem faltar."""
    return {r["name"] for r in con.execute(f"PRAGMA table_info({tabela})")}


def _resolver_experimentos(con: sqlite3.Connection, pedidos: list[int] | None, avisos: list[str]) -> tuple[list[int], list[dict]]:
    """Experimentos a analisar e os EXCLUIDOS por deriva (coleta_consulta.descartado_por_deriva = 1, listados a parte).
    `pedidos=None` = todos os do arquivo."""
    todos = [r["experiment_id"] for r in con.execute("SELECT experiment_id FROM experiment ORDER BY experiment_id")]
    if pedidos is None:
        pedidos = todos
    pedidos = list(dict.fromkeys(int(e) for e in pedidos))
    if not pedidos:
        raise ValueError("nenhum experimento para analisar (arquivo vazio ou lista vazia)")
    faltam = [e for e in pedidos if e not in todos]
    if faltam:
        raise KeyError(f"experimento(s) inexistente(s): {faltam}")
    descartados: list[dict] = []
    if {"experiment_id", "descartado_por_deriva", "rodada", "query_id"} <= _colunas(con, "coleta_consulta"):
        ph = ",".join("?" for _ in pedidos)
        for r in con.execute(f"SELECT rodada, query_id, experiment_id, tentativa, deriva_a1_a2 FROM coleta_consulta "
                             f"WHERE descartado_por_deriva = 1 AND experiment_id IN ({ph}) ORDER BY experiment_id", pedidos):
            descartados.append({"rodada": r["rodada"], "query_id": r["query_id"], "experiment_id": r["experiment_id"],
                                "tentativa": r["tentativa"], "deriva_a1_a2": r["deriva_a1_a2"]})
    fora = {d["experiment_id"] for d in descartados}
    usados = [e for e in pedidos if e not in fora]
    if fora:
        avisos.append(f"experimento(s) {sorted(fora)} EXCLUIDO(S) da analise por deriva A1->A2 acima do limite "
                      "(coleta_consulta.descartado_por_deriva = 1); listados em descartados.csv")
    if not usados:
        raise ValueError(f"todos os experimentos pedidos foram descartados por deriva: {sorted(fora)}")
    return usados, descartados


def _carregar_escrita(con: sqlite3.Connection, usados: list[int], rotulos: dict[tuple[int, str], str], crit: Criterios,
                      avisos: list[str]) -> tuple[list[dict], dict[tuple[int, str], float]]:
    """Custo de escrita do indice da prescricao (tabelas escrita_medicao e escrita_indice do coletor): razao
    com indice / sem indice por operacao e metrica, SEM limiar. Devolve as linhas e, por (experimento, query_id), o
    maior valor de `razao_reg` entre as operacoes na metrica `Criterios.metrica_escrita` (usado so no veredito)."""
    if not ({"rodada", "query_id", "experiment_id"} <= _colunas(con, "coleta_consulta")
            and {"rodada", "query_id", "indice", "operacao", "rep"} <= _colunas(con, "escrita_medicao")):
        return [], {}
    ph = ",".join("?" for _ in usados)
    mapa = {(r["rodada"], r["query_id"]): r["experiment_id"] for r in con.execute(
        f"SELECT rodada, query_id, experiment_id FROM coleta_consulta WHERE experiment_id IN ({ph})", usados)}
    info = {}
    if {"rodada", "query_id", "indice", "paginas", "criacao_ms"} <= _colunas(con, "escrita_indice"):
        info = {(r["rodada"], r["query_id"], r["indice"]): (r["paginas"], r["criacao_ms"]) for r in con.execute("SELECT * FROM escrita_indice")}
    grupos: dict[tuple, dict[str, list[float]]] = {}
    tabela_de: dict[tuple, str] = {}
    ks: dict[tuple, int] = {}
    for r in con.execute("SELECT * FROM escrita_medicao ORDER BY rodada, query_id, indice, operacao, rep"):
        if (r["rodada"], r["query_id"]) not in mapa:
            continue
        chave = (r["rodada"], r["query_id"], r["indice"], r["operacao"])
        tabela_de[chave], ks[chave] = r["tabela"], r["k"]
        for m in METRICAS_ESCRITA:
            if r[m] is not None:
                grupos.setdefault(chave, {}).setdefault(m, []).append(float(r[m]))
    linhas: list[dict] = []
    razao_por: dict[tuple[int, str], float] = {}
    for (rod, qid, indice, op), medidas in sorted(grupos.items()):
        if indice == "sem indice":
            continue
        sem = grupos.get((rod, qid, "sem indice", op))
        if not sem:
            avisos.append(f"escrita de {qid} ({op}): sem a medicao 'sem indice' para comparar")
            continue
        exp = mapa[(rod, qid)]
        pag, cri = info.get((rod, qid, indice), (None, None))
        for m in METRICAS_ESCRITA:
            if m not in medidas or m not in sem:
                continue
            ms, mc = float(np.median(sem[m])), float(np.median(medidas[m]))
            rz = razao_medianas(ms, mc, crit.piso_zero)
            linhas.append({"experiment_id": exp, "rodada": rod, "query_id": qid, "consulta": rotulos.get((exp, qid), qid),
                           "tabela": tabela_de[(rod, qid, indice, op)], "indice": indice, "operacao": op, "k": ks[(rod, qid, indice, op)],
                           "metrica": m, "n_sem": len(sem[m]), "n_com": len(medidas[m]), "mediana_sem": ms, "mediana_com": mc,
                           "razao": rz.bruta, "razao_reg": rz.reg, "regra_zero": rz.regra, "aumento_pct": None if rz.bruta is None else (rz.bruta - 1) * 100,
                           "indice_paginas": pag, "criacao_ms": cri})
            if m == crit.metrica_escrita:
                razao_por[(exp, qid)] = max(razao_por.get((exp, qid), 0.0), rz.reg)
    return linhas, razao_por


# ----------------------------------------------------------------------------------------- analise
def _mediana(x: np.ndarray) -> float:
    return float(np.median(x))


def _sem_atipicos(g: Grupo, metrica: str) -> tuple[np.ndarray, list[int]]:
    pares = g.pares(metrica)
    if not pares:
        return np.array([]), []
    r = resumir([p[2] for p in pares])
    marcados = set(r.atipicos)
    return np.array([p[2] for i, p in enumerate(pares) if i not in marcados], dtype=float), sorted(marcados)


def _resumo_grupo(g: Grupo, metrica: str, cons: Consulta, crit: Criterios, papel: str):
    pares = g.pares(metrica)
    if not pares:
        return None, []
    x = np.array([p[2] for p in pares])
    r = resumir([p[2] for p in pares])
    lo, hi = ic_mediana(x, crit, g.experiment_id, g.query_id, g.variant_id, g.regime, metrica)
    meia = (hi - lo) / 2 / r.mediana * 100 if r.mediana else None
    sp = shapiro_p(x)
    iqr = r.q3 - r.q1
    lim = (r.q1 - FATOR_TUKEY * iqr, r.q3 + FATOR_TUKEY * iqr)
    linha = {"experiment_id": g.experiment_id, "query_id": g.query_id, "consulta": cons.rotulo, "antipadrao": cons.antipadrao,
             "variant_id": g.variant_id, "variante": g.variante, "kind": g.kind, "papel": papel, "regime": g.regime,
             "metrica": metrica, "n": r.n, "minimo": r.minimo, "q1": r.q1, "mediana": r.mediana, "q3": r.q3,
             "maximo": r.maximo, "media": r.media, "dp": r.dp, "cv": r.cv, "ic_lo": lo, "ic_hi": hi,
             "meia_largura_pct": meia, "atipicos": len(r.atipicos), "shapiro_p": sp,
             "normalidade_rejeitada": None if sp is None else sp < crit.alfa,
             "n_runs": len(g.runs), "runs_com_aviso": sum(1 for x_ in g.runs if x_.com_aviso),
             "run_ids": ";".join(map(str, g.run_ids))}
    atip = [{"experiment_id": g.experiment_id, "query_id": g.query_id, "consulta": cons.rotulo, "variante": g.variante,
             "regime": g.regime, "metrica": metrica, "run_id": pares[i][0], "rep": pares[i][1], "valor": pares[i][2],
             "limite_inf": lim[0], "limite_sup": lim[1]} for i in r.atipicos]
    return linha, atip


def _deriva(base: Grupo, metrica: str) -> float | None:
    """Mediana do ultimo bloco da base contra o primeiro (ABA: A2 contra A1), em %. None se so ha um bloco."""
    if len(base.runs) < 2:
        return None
    ordenados = sorted(base.runs, key=lambda r: r.seq)
    a = [float(v) for v in ordenados[0].valores.get(metrica, []) if v is not None]
    b = [float(v) for v in ordenados[-1].valores.get(metrica, []) if v is not None]
    if not a or not b or float(np.median(a)) == 0:
        return None
    return (float(np.median(b)) / float(np.median(a)) - 1) * 100


def _comparar(base: Grupo, trat: Grupo, metrica: str, cons: Consulta, crit: Criterios, no_agregado: bool,
              comparacao: str = "principal", grupo: str | None = None):
    """comparacao: principal (tratamento do framework contra a baseline), secundaria (outros bracos contra a baseline)
    ou nativo_vs_prescricao (baseline = tratamento principal). grupo: rotulo de agregacao entre consultas."""
    xa, xd = base.vetor(metrica), trat.vetor(metrica)
    if len(xa) == 0 or len(xd) == 0:
        return None
    chave = (base.experiment_id, base.query_id, base.variant_id, trat.variant_id, base.regime, metrica)
    ma, md = _mediana(xa), _mediana(xd)
    rz = razao_medianas(ma, md, crit.piso_zero)
    lo, hi = ic_razao(xa, xd, crit, *chave)
    sa, ia = _sem_atipicos(base, metrica)
    sd, id_ = _sem_atipicos(trat, metrica)
    mas, mds = _mediana(sa), _mediana(sd)
    rzs = razao_medianas(mas, mds, crit.piso_zero)
    los, his = ic_razao(sa, sd, crit, *chave, "sem_atipicos")
    deriva = _deriva(base, metrica)
    return {
        "experiment_id": base.experiment_id, "query_id": base.query_id, "consulta": cons.rotulo, "antipadrao": cons.antipadrao,
        "seletividade": cons.seletividade, "papel": cons.papel, "comparacao": comparacao, "grupo": grupo or trat.kind,
        "regime": base.regime, "metrica": metrica,
        "baseline_variant_id": base.variant_id, "baseline": base.variante,
        "tratamento_variant_id": trat.variant_id, "tratamento": trat.variante, "tratamento_kind": trat.kind,
        "n_antes": len(xa), "n_depois": len(xd), "mediana_antes": ma, "mediana_depois": md,
        "razao": rz.bruta, "razao_reg": rz.reg, "regra_zero": rz.regra, "reducao_pct": rz.reducao_pct,
        "ic_lo": lo, "ic_hi": hi, "reducao_ic_lo": (1 - hi) * 100, "reducao_ic_hi": (1 - lo) * 100,
        "atipicos_antes": len(ia), "atipicos_depois": len(id_),
        "razao_sem_atipicos": rzs.bruta, "razao_reg_sem_atipicos": rzs.reg, "regra_zero_sem_atipicos": rzs.regra,
        "reducao_pct_sem_atipicos": rzs.reducao_pct, "ic_lo_sem_atipicos": los, "ic_hi_sem_atipicos": his,
        "deriva_base_pct": deriva,
        "deriva_alerta": None if deriva is None else abs(deriva) > crit.limite_deriva_pct,
        "no_agregado": no_agregado,
        "run_ids_antes": ";".join(map(str, base.run_ids)), "run_ids_depois": ";".join(map(str, trat.run_ids)),
    }


def _reducao_criterio(c: dict, sufixo: str = "") -> float:
    bruta = c["reducao_pct" + sufixo]
    return bruta if bruta is not None else (1 - c["razao_reg" + sufixo]) * 100


def _veredicto(c: dict, escrita: dict[tuple[int, str], float], crit: Criterios) -> dict:
    def avalia(sufixo: str):
        red = _reducao_criterio(c, sufixo)
        reducao_ok = red >= crit.reducao_minima_pct
        ic_ok = c["ic_hi" + sufixo] < 1.0   # IC da razao exclui 1 na direcao da melhoria
        return reducao_ok, ic_ok, red

    reducao_ok, ic_ok, red = avalia("")
    reducao_ok_s, ic_ok_s, _ = avalia("_sem_atipicos")
    # custo de escrita: so existe para o indice da prescricao (comparacao principal); razao com/sem indice
    razao_w = escrita.get((c["experiment_id"], c["query_id"])) if c["comparacao"] == "principal" else None
    if razao_w is None:
        estado_w = "sem_dados"
    elif crit.degradacao_escrita_max_pct is None:
        estado_w = "sem_limiar"          # razao reportada, nenhum limiar aplicado (PRE-REGISTRO.md)
    else:
        estado_w = "degradado" if razao_w > 1 + crit.degradacao_escrita_max_pct / 100 else "ok"
    ok_w = estado_w != "degradado"
    melhoria = bool(reducao_ok and ic_ok and ok_w)
    melhoria_s = bool(reducao_ok_s and ic_ok_s and ok_w)
    return {
        "experiment_id": c["experiment_id"], "query_id": c["query_id"], "consulta": c["consulta"],
        "antipadrao": c["antipadrao"], "seletividade": c["seletividade"], "papel": c["papel"], "regime": c["regime"],
        "comparacao": c["comparacao"], "grupo": c["grupo"],
        "tratamento_variant_id": c["tratamento_variant_id"], "tratamento": c["tratamento"],
        "tratamento_kind": c["tratamento_kind"], "metrica_criterio": c["metrica"],
        "mediana_antes": c["mediana_antes"], "mediana_depois": c["mediana_depois"], "razao": c["razao"],
        "razao_reg": c["razao_reg"], "ic_lo": c["ic_lo"], "ic_hi": c["ic_hi"], "reducao_pct": c["reducao_pct"],
        "reducao_ok": reducao_ok, "ic_exclui_1": ic_ok, "piora": c["razao_reg"] > 1.0,
        "escrita_razao": None if razao_w is None or math.isinf(razao_w) else razao_w, "criterio_escrita": estado_w,
        "melhoria_significativa": melhoria, "melhoria_significativa_sem_atipicos": melhoria_s,
        "veredito_muda_sem_atipicos": melhoria != melhoria_s,
        "deriva_alerta": c["deriva_alerta"], "regra_zero": c["regra_zero"],
        "run_ids_antes": c["run_ids_antes"], "run_ids_depois": c["run_ids_depois"],
    }


def _determinismo(g: Grupo, cons: Consulta, crit: Criterios) -> dict | None:
    pares = g.pares("logical_reads")
    if not pares:
        return None
    x = [p[2] for p in pares]
    r = resumir(x)
    distintos = sorted(set(x))
    planos = sorted({rd.query_plan_hash for rd in g.runs if rd.query_plan_hash})
    dops = sorted({rd.dop for rd in g.runs if rd.dop is not None})
    ra = sorted({v for rd in g.runs for v in rd.valores.get("read_ahead_reads", []) if v is not None})
    determ = r.dp == 0
    alerta = ""
    if not determ:
        alerta = (f"dp > 0: investigar plano ({len(planos)} QueryPlanHash), paralelismo (DOP {dops or 'n/d'}) e "
                  f"read-ahead ({len(ra)} valor(es) distinto(s))")
    return {"experiment_id": g.experiment_id, "query_id": g.query_id, "consulta": cons.rotulo, "variante": g.variante,
            "kind": g.kind, "regime": g.regime, "n": r.n, "minimo": r.minimo, "mediana": r.mediana, "maximo": r.maximo, "dp": r.dp,
            "valores_distintos": len(distintos), "deterministico": determ, "planos_distintos": len(planos),
            "dop": ";".join(map(str, dops)), "read_ahead_distintos": len(ra), "alerta": alerta,
            "run_ids": ";".join(map(str, g.run_ids))}


def _conjuntos(base: list[dict], crit: Criterios) -> list[tuple[str, object, str]]:
    """(nome, filtro, sufixo das colunas). principal e sem_atipicos: tratamento principal das consultas de papel principal.
    Se houver controles: com_controles (todas as consultas, sensibilidade) e controles (so eles). Se houver bracos
    secundarios: secundarias (reescrita, indice, nativo, contraexemplo..., fora do agregado principal)."""
    papel_ok = lambda c: c["papel"] in crit.papeis_principais  # noqa: E731
    princ = lambda c: c["comparacao"] == "principal"  # noqa: E731
    conj = [("principal", lambda c: princ(c) and papel_ok(c), ""), ("sem_atipicos", lambda c: princ(c) and papel_ok(c), "_sem_atipicos")]
    if any(princ(c) and not papel_ok(c) for c in base):
        conj += [("com_controles", princ, ""), ("controles", lambda c: princ(c) and not papel_ok(c), "")]
    if any(not princ(c) for c in base):
        conj += [("secundarias", lambda c: not princ(c) and papel_ok(c), "")]
    return conj


def _agregar(comparacoes: list[dict], crit: Criterios) -> list[dict]:
    base = [c for c in comparacoes if c["no_agregado"] and c["metrica"] in crit.metricas_teste]
    # estratos: todas, por antipadrao e por classe de seletividade
    def estratos(c):
        yield "todas", "(todas)"
        yield "antipadrao", c["antipadrao"]
        if c["seletividade"]:
            yield "seletividade", c["seletividade"]

    saida: list[dict] = []
    for analise, filtro, suf in _conjuntos(base, crit):
        familias: dict[tuple, dict[str, list[dict]]] = {}
        for c in (c for c in base if filtro(c)):
            for et, ev in estratos(c):
                familias.setdefault((c["regime"], c["grupo"], et, ev), {}).setdefault(c["metrica"], []).append(c)
        for (regime, kind, et, ev), por_metrica in sorted(familias.items()):
            linhas = []
            for metrica in crit.metricas_teste:
                cs = por_metrica.get(metrica, [])
                if not cs:
                    continue
                razoes = [c["razao_reg" + suf] for c in cs]
                w = wilcoxon_log_razao(razoes, crit)
                ef = efeito_entre_consultas(razoes, crit, analise, regime, kind, et, ev, metrica)
                sp = shapiro_p(np.log(np.asarray(razoes)))
                obs = []
                if len(cs) < crit.n_min_consultas:
                    obs.append(f"n={len(cs)} consultas (< {crit.n_min_consultas}): exploratorio")
                if w["p_minimo"] > crit.alfa:
                    obs.append(f"p minimo atingivel {w['p_minimo']:.4g} > alfa: o teste NAO pode rejeitar H0 "
                               f"(n efetivo = {w['n_efetivo']})")
                elif w["p_minimo"] > crit.alfa / len(crit.metricas_teste):
                    obs.append(f"p minimo atingivel {w['p_minimo']:.4g} > alfa/{len(crit.metricas_teste)}: "
                               "nao sobrevive a correcao de Holm")
                if w["n_zeros"]:
                    obs.append(f"{w['n_zeros']} consulta(s) com razao exatamente 1 descartada(s) do Wilcoxon")
                cotas = sum(1 for c in cs if c["regra_zero" + suf] not in ("", "ambas_zero"))
                if cotas:
                    obs.append(f"{cotas} consulta(s) com mediana zero: razao e cota calculada com piso {crit.piso_zero:g}")
                linhas.append({
                    "analise": analise, "regime": regime, "grupo": kind, "estrato_tipo": et, "estrato": ev,
                    "metrica": metrica, "n_consultas": len(cs), "n_efetivo": w["n_efetivo"], "n_reduz": w["n_reduz"],
                    "n_aumenta": w["n_aumenta"], **ef, "wilcoxon_estatistica": w["estatistica"], "p_bruto": w["p"],
                    "p_holm": None, "metodo_p": w["metodo_p"], "p_minimo": w["p_minimo"],
                    "poder_insuficiente": w["p_minimo"] > crit.alfa, "significativo_holm": None,
                    "shapiro_p_lograzao": sp, "normalidade_rejeitada": None if sp is None else sp < crit.alfa,
                    "observacao": "; ".join(obs), "consultas": ";".join(c["consulta"] for c in cs),
                    "experimentos": ";".join(map(str, sorted({c["experiment_id"] for c in cs}))),
                    "run_ids": ";".join(sorted({r for c in cs for r in (c["run_ids_antes"] + ";" + c["run_ids_depois"]).split(";")},
                                               key=int)),
                })
            ajust = holm([l["p_bruto"] for l in linhas], m=len(crit.metricas_teste))
            for l, pa in zip(linhas, ajust):
                l["p_holm"] = pa
                l["significativo_holm"] = None if pa is None else pa < crit.alfa
            saida += linhas
    return saida


def _escolher_prescricao(tratas: list[Grupo], crit: Criterios) -> Grupo | None:
    """Tratamento principal da consulta: a variante do 1o kind de `kinds_prescricao` que existir (prescricao, senao
    indice); entre duas do mesmo kind, a de menor variant_id. None = consulta sem esses kinds (uso generico)."""
    for k in crit.kinds_prescricao:
        cand = sorted((g for g in tratas if g.kind == k), key=lambda g: g.variant_id)
        if cand:
            return cand[0]
    return None


def _gerar_comparacoes(por_consulta, consultas, crit: Criterios, avisos: list[str]) -> list[dict]:
    """Comparacoes baseline -> tratamento, por consulta, regime e metrica. Papeis (PRE-REGISTRO.md):
    principal = prescricao (ou indice se nao ha reescrita) contra a baseline; secundaria = cada outro braco contra a
    baseline; nativo_vs_prescricao = braco nativo contra o tratamento principal. So a principal vai ao agregado principal.
    Sem nenhum kind de `kinds_prescricao` (uso generico, kinds livres), todo tratamento e principal e agrupa pelo kind."""
    saida: list[dict] = []
    for (exp, qid), gs in sorted(por_consulta.items()):
        cons = consultas[(exp, qid)]
        for regime in sorted({g.regime for g in gs}):
            doreg = [g for g in gs if g.regime == regime]
            bases = sorted((g for g in doreg if g.kind == crit.kind_baseline), key=lambda g: g.variant_id)
            tratas = sorted((g for g in doreg if g.kind != crit.kind_baseline), key=lambda g: g.variant_id)
            if not bases:
                avisos.append(f"{cons.rotulo} [{regime}]: sem variante de kind '{crit.kind_baseline}': sem comparacoes")
                continue
            if len(bases) > 1:
                avisos.append(f"{cons.rotulo} [{regime}]: {len(bases)} variantes baseline; usada a de menor variant_id")
            if not tratas:
                avisos.append(f"{cons.rotulo} [{regime}]: sem variante de tratamento")
                continue
            escolhido = _escolher_prescricao(tratas, crit)
            vistos: set[str] = set()
            for t in tratas:
                if escolhido is None:
                    comparacao, grupo = "principal", t.kind
                elif t is escolhido:
                    comparacao, grupo = "principal", PRESCRICAO
                else:
                    comparacao, grupo = "secundaria", t.kind
                primeira = t.kind not in vistos
                if not primeira:
                    avisos.append(f"{cons.rotulo} [{regime}]: mais de uma variante de kind '{t.kind}'; so a de menor "
                                  f"variant_id entra no agregado entre consultas (a variante {t.variante} fica so em comparisons)")
                vistos.add(t.kind)
                for m in crit.metricas_resumo:
                    c = _comparar(bases[0], t, m, cons, crit, primeira, comparacao, grupo)
                    if c:
                        saida.append(c)
                if escolhido is not None and t.kind == crit.kind_nativo and t is not escolhido and primeira:
                    for m in crit.metricas_resumo:   # nativo contra o tratamento principal (a pergunta do desenho fatorial)
                        c = _comparar(escolhido, t, m, cons, crit, True, "nativo_vs_prescricao", "nativo_vs_prescricao")
                        if c:
                            saida.append(c)
    return saida


def _consistencia_entre_regimes(determ: list[dict], crit: Criterios, avisos: list[str]) -> None:
    """Leituras logicas podem diferir entre quente e frio (key lookup, read-ahead): marca e avisa."""
    por_var: dict[tuple, dict[str, float]] = {}
    for d in determ:
        por_var.setdefault((d["experiment_id"], d["query_id"], d["variante"]), {})[d["regime"]] = d["mediana"]
    diferem = {k for k, v in por_var.items() if len(v) > 1 and len(set(v.values())) > 1}
    for d in determ:
        k = (d["experiment_id"], d["query_id"], d["variante"])
        d["difere_entre_regimes"] = k in diferem
        d["medianas_por_regime"] = ";".join(f"{r}={v:g}" for r, v in sorted(por_var[k].items()))
    if diferem:
        avisos.append(f"leituras logicas DIFEREM entre regimes em {len(diferem)} variante(s) (ex.: key lookup, read-ahead; ver "
                      f"determinism.csv): a analise principal usa o regime '{crit.regime_principal}'; o outro sai em tabela propria")


def analisar(caminho: str | Path, experimentos: list[int] | None = None, criterios: Criterios | None = None,
             antipadroes: dict[str, str] | None = None, papeis: dict[str, str] | None = None) -> Analise:
    """Analisa os experimentos do SQLite `caminho` (somente leitura; `None` = todos os do arquivo). Experimentos
    descartados por deriva pelo coletor sao excluidos e listados a parte. `antipadroes`: query_id -> antipadrao e
    `papeis`: query_id -> principal|controle (opcionais; prevalecem sobre catalog_query.antipattern_id e .notes)."""
    crit = criterios or Criterios()
    avisos: list[str] = []
    caminho = Path(caminho)
    con = _abrir_somente_leitura(caminho)
    try:
        experimentos, descartados = _resolver_experimentos(con, experimentos, avisos)
        consultas, grupos, exps, envs = _carregar(con, experimentos, crit, antipadroes, avisos, papeis)
        escrita_linhas, escrita_por = _carregar_escrita(con, experimentos, {k: c.rotulo for k, c in consultas.items()}, crit, avisos)
    finally:
        con.close()

    resumos: list[dict] = []
    atipicos: list[dict] = []
    determ: list[dict] = []
    papel = lambda g: "baseline" if g.kind == crit.kind_baseline else "tratamento"  # noqa: E731
    for gk in sorted(grupos):
        g = grupos[gk]
        cons = consultas[(g.experiment_id, g.query_id)]
        for m in crit.metricas_resumo:
            linha, atip = _resumo_grupo(g, m, cons, crit, papel(g))
            if linha:
                resumos.append(linha)
                atipicos += atip
        d = _determinismo(g, cons, crit)
        if d:
            determ.append(d)
    _consistencia_entre_regimes(determ, crit, avisos)

    por_consulta: dict[tuple[int, str], list[Grupo]] = {}
    for gk in sorted(grupos):
        por_consulta.setdefault((gk[0], gk[1]), []).append(grupos[gk])
    comparacoes = _gerar_comparacoes(por_consulta, consultas, crit, avisos)

    veredictos = [_veredicto(c, escrita_por, crit) for c in comparacoes
                  if c["metrica"] == crit.metrica_criterio and c["comparacao"] != "nativo_vs_prescricao"]
    agregados = _agregar(comparacoes, crit)
    # contagem de melhorias por estrato (so metrica do criterio; nativo_vs_prescricao nao tem veredito)
    for a in agregados:
        if a["metrica"] == crit.metrica_criterio and a["grupo"] != "nativo_vs_prescricao":
            sel = [v for v in veredictos if v["regime"] == a["regime"] and v["grupo"] == a["grupo"]
                   and v["consulta"] in a["consultas"].split(";") and _no_estrato(v, a)]
            chave = "melhoria_significativa_sem_atipicos" if a["analise"] == "sem_atipicos" else "melhoria_significativa"
            a["n_melhoria_significativa"] = sum(1 for v in sel if v[chave])
        else:
            a["n_melhoria_significativa"] = None

    fora = sorted({c.rotulo for c in consultas.values() if c.papel not in crit.papeis_principais})
    if fora:
        avisos.append(f"consulta(s) com papel fora de {list(crit.papeis_principais)} ({fora}): ficam fora da analise principal; "
                      "aparecem em 'controles' e 'com_controles' (aggregate.csv) e na tabela de controles")
    nd = [d for d in determ if not d["deterministico"]]
    if nd:
        avisos.append(f"leituras logicas NAO deterministicas em {len(nd)} de {len(determ)} grupo(s) (regimes: "
                      f"{sorted({d['regime'] for d in nd})}; ver determinism.csv): investigar antes de reportar")
    regimes = sorted({g.regime for g in grupos.values()})
    if crit.regime_principal not in regimes:
        avisos.append(f"regime principal '{crit.regime_principal}' ausente dos dados (regimes: {regimes})")
    n_principais = len({c["consulta"] for c in comparacoes if c["no_agregado"] and c["comparacao"] == "principal"
                        and c["papel"] in crit.papeis_principais})
    if n_principais < crit.n_min_consultas:
        avisos.append(f"{n_principais} consulta(s) na analise principal: abaixo de n_min_consultas="
                      f"{crit.n_min_consultas}; resultado entre consultas e exploratorio e o Wilcoxon pode nao ter poder "
                      "para rejeitar H0 (ver p_minimo em aggregate.csv)")
    meta = {"versao_analise": VERSAO_ANALISE, "tcclab": __version__, "python": platform.python_version(),
            "numpy": np.__version__, "scipy": scipy.__version__,
            "banco": caminho.name, "banco_sha256": _sha_arquivo(caminho.resolve()), "esquema": VERSAO_ESQUEMA,
            "experimentos": experimentos, "descartados_ids": sorted(d["experiment_id"] for d in descartados),
            "env_ids": sorted({exps[e]["env_id"] for e in experimentos}),
            "gerado_em_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}
    cons_rows = [{"experiment_id": c.experiment_id, "query_id": c.query_id, "consulta": c.rotulo, "titulo": c.titulo,
                  "antipadrao": c.antipadrao, "seletividade": c.seletividade, "papel": c.papel} for c in consultas.values()]
    return Analise(crit, meta, avisos, cons_rows, resumos, comparacoes, veredictos, agregados, determ, atipicos,
                   escrita=escrita_linhas, descartados=descartados, grupos=grupos)


def _no_estrato(v: dict, a: dict) -> bool:
    if a["estrato_tipo"] == "todas":
        return True
    campo = "antipadrao" if a["estrato_tipo"] == "antipadrao" else "seletividade"
    return v[campo] == a["estrato"]


# ----------------------------------------------------------------------------------------- persistencia opcional
DDL_ANALISE = """
CREATE TABLE IF NOT EXISTS analise_execucao (
  an_id INTEGER PRIMARY KEY AUTOINCREMENT, criado_em_utc TEXT, experimentos_json TEXT, criterios_json TEXT,
  criterios_sha TEXT, semente INTEGER, versoes_json TEXT, resultado_json TEXT)"""


def json_seguro(o):
    if isinstance(o, (np.floating, float)):
        return None if (math.isnan(o) or math.isinf(o)) else float(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, dict):
        return {k: json_seguro(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [json_seguro(v) for v in o]
    return o


def para_json(analise: Analise) -> str:
    return json.dumps(json_seguro(analise.como_dict()), ensure_ascii=False, indent=2)


def gravar_no_banco(caminho: str | Path, analise: Analise) -> int:
    """Grava o resultado numa tabela AUXILIAR `analise_execucao` (criada se faltar; nao altera o esquema v2 nem a
    user_version; as tabelas de medicao nao sao tocadas). Devolve o an_id."""
    con = sqlite3.connect(Path(caminho))
    try:
        con.execute(DDL_ANALISE)
        cur = con.execute(
            "INSERT INTO analise_execucao(criado_em_utc, experimentos_json, criterios_json, criterios_sha, semente, "
            "versoes_json, resultado_json) VALUES (?,?,?,?,?,?,?)",
            (analise.meta["gerado_em_utc"], json.dumps(analise.meta["experimentos"]),
             json.dumps(analise.criterios.como_dict()), analise.criterios.sha(), analise.criterios.semente,
             json.dumps({k: analise.meta[k] for k in ("versao_analise", "tcclab", "python", "numpy", "scipy")}),
             para_json(analise)))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()
