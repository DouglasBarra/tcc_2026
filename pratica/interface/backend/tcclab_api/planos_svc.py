"""Planos de execucao: leitura do XML salvo (rodada ou experimento/planos) e resumo de operadores via tcclab.plano."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

from tcclab.plano import analisar_plano

from .config import Configuracao
from .leitura import RodadaRef

SHA = re.compile(r"^[0-9a-f]{8,64}$")
ROTULO_PLANO = re.compile(r"^(original|reescrita)\.(estimado|real)$")
ID_CONSULTA = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,20}$")
LIMITE_XML = 5_000_000


def ler_plano_da_rodada(ref: RodadaRef, linha_plan: dict) -> tuple[str | None, str | None]:
    """XML do plano gravado por tcclab.store.gravar_plano. Procura em <rodada>/tcclab_planos/<xml_sha>.sqlplan.
    O caminho absoluto gravado no banco (`plan.path`) NAO e seguido: a pasta pode ter sido movida e o valor vem de arquivo."""
    sha = (linha_plan.get("xml_sha") or "").lower()
    if not SHA.match(sha):
        return None, "identificador do plano inválido"
    arq = ref.pasta / "tcclab_planos" / f"{sha}.sqlplan"
    if not arq.is_file():
        return None, f"arquivo do plano não encontrado em {arq.parent.name}/{arq.name}"
    if arq.stat().st_size > LIMITE_XML:
        return None, "arquivo do plano excede o limite de leitura"
    return arq.read_text(encoding="utf-8"), None


def _operador(o) -> dict:
    razao = (o.est_rows / o.actual_rows) if (o.est_rows is not None and o.tem_contadores_reais and o.actual_rows) else None
    return {
        "node_id": o.node_id, "operador_fisico": o.physical_op, "operador_logico": o.logical_op, "objeto": o.objeto,
        "tipo_indice": o.tipo_indice, "linhas_estimadas": o.est_rows, "linhas_lidas_estimadas": o.est_rows_read,
        "cardinalidade_tabela": o.table_cardinality, "custo_subarvore": o.est_subtree_cost,
        "tem_contadores_reais": o.tem_contadores_reais, "linhas_reais": o.actual_rows if o.tem_contadores_reais else None,
        "linhas_lidas_reais": o.actual_rows_read if o.tem_contadores_reais else None,
        "execucoes": o.actual_executions if o.tem_contadores_reais else None,
        "leituras_logicas": o.actual_logical_reads if o.tem_contadores_reais else None,
        "leituras_fisicas": o.actual_physical_reads if o.tem_contadores_reais else None,
        "predicado": o.predicado, "lookup": o.lookup,
        # fracao das linhas da tabela que o operador devolve (reais ou estimadas); quociente de dois campos do proprio XML
        "fracao_real": (o.actual_rows / o.table_cardinality) if (o.tem_contadores_reais and o.table_cardinality) else None,
        "fracao_estimada": (o.est_rows / o.table_cardinality) if (o.est_rows is not None and o.table_cardinality) else None,
        # estimado/real: indicador de qualidade da estimativa (fora de [1/10, 10] sugere estatistica ruim); derivado dos dois campos acima
        "razao_estimado_real": razao,
    }


_NS = "{http://schemas.microsoft.com/sqlserver/2004/07/showplan}"


def _estatisticas_usadas(xml: str) -> list[dict]:
    """OptimizerStatsUsage/StatisticsInfo do plano: estatisticas que o otimizador leu (tcclab.plano nao as extrai)."""
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError:
        return []
    saida = []
    for si in raiz.iter(_NS + "StatisticsInfo"):
        def num(k, tipo=float):
            v = si.get(k)
            try:
                return tipo(v) if v not in (None, "") else None
            except ValueError:
                return None
        saida.append({"tabela": ".".join(x.strip("[]") for x in (si.get("Schema"), si.get("Table")) if x) or None,
                      "estatistica": (si.get("Statistics") or "").strip("[]") or None,
                      "ultima_atualizacao": si.get("LastUpdate"), "modificacoes": num("ModificationCount", int),
                      "amostragem_pct": num("SamplingPercent")})
    return saida


def resumir_plano(xml: str) -> dict:
    """Resumo do plano principal: atributos da instrucao e tabela de operadores (todos os campos vem do XML)."""
    try:
        pa = analisar_plano(xml)
    except Exception as e:  # noqa: BLE001 - XML malformado
        return {"valido": False, "motivo": f"XML do plano não pôde ser lido: {e}"}
    ins = pa.principal()
    if ins is None:
        return {"valido": False, "motivo": "o XML não contém instrução com plano"}
    return {
        "valido": True, "versao_showplan": pa.versao, "build": pa.build,
        "tipo_instrucao": ins.tipo, "query_hash": ins.query_hash, "query_plan_hash": ins.query_plan_hash,
        "custo_subarvore": ins.custo_subarvore, "linhas_estimadas": ins.est_rows,
        "nivel_otimizacao": ins.nivel_otimizacao, "versao_ce": ins.versao_ce, "grau_paralelismo": ins.grau_paralelismo,
        "compile_time_ms": ins.compile_time_ms, "compile_cpu_ms": ins.compile_cpu_ms,
        "eh_plano_real": ins.eh_plano_real, "cpu_ms_plano": ins.cpu_ms, "elapsed_ms_plano": ins.elapsed_ms,
        "leituras_logicas_plano": ins.leituras_logicas_plano if ins.eh_plano_real else None,
        "avisos": ins.avisos, "indices_ausentes": ins.indices_ausentes, "parametros": ins.parametros,
        "operadores": [_operador(o) for o in ins.operadores],
        "texto_instrucao": ins.texto,
        "estatisticas_usadas": _estatisticas_usadas(xml),
    }


# ---------------------------------------------------------------------------------------- planos salvos (experimento/planos)
def pastas_de_planos(cfg: Configuracao) -> list[Path]:
    """experimento/planos (catalogo) e experimento/planos_controles (controles limpos)."""
    return [cfg.planos, cfg.planos_controles]


def indice_planos_salvos(cfg: Configuracao) -> dict:
    saida: dict = {}
    for pasta in pastas_de_planos(cfg):
        arq = pasta / "indice.json"
        if arq.is_file():
            try:
                saida.update(json.loads(arq.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return saida


def _pasta_da_consulta(cfg: Configuracao, query_id: str) -> Path | None:
    if not ID_CONSULTA.match(query_id):
        return None
    return next((p / query_id for p in pastas_de_planos(cfg) if (p / query_id).is_dir()), None)


def planos_salvos_da_consulta(cfg: Configuracao, query_id: str) -> list[dict]:
    """Rotulos disponiveis ('original.estimado', ...) para a consulta, conforme os arquivos existentes na pasta."""
    pasta = _pasta_da_consulta(cfg, query_id)
    if pasta is None:
        return []
    idx = indice_planos_salvos(cfg).get(query_id, {}).get("planos", {})
    saida = []
    for f in sorted(pasta.glob("*.sqlplan")):
        rotulo = f.name[: -len(".sqlplan")]
        if ROTULO_PLANO.match(rotulo):
            saida.append({"rotulo": rotulo, "arquivo": f"{pasta.parent.name}/{query_id}/{f.name}", "indice": idx.get(rotulo)})
    return saida


def ler_plano_salvo(cfg: Configuracao, query_id: str, rotulo: str) -> tuple[str | None, str | None]:
    if not ID_CONSULTA.match(query_id) or not ROTULO_PLANO.match(rotulo):
        return None, "consulta ou rótulo de plano inválido"
    pasta = _pasta_da_consulta(cfg, query_id)
    arq = pasta / f"{rotulo}.sqlplan" if pasta else None
    if arq is None or not arq.is_file():
        return None, "plano salvo não encontrado"
    if arq.stat().st_size > LIMITE_XML:
        return None, "arquivo do plano excede o limite de leitura"
    return arq.read_text(encoding="utf-8"), None
