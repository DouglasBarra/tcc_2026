"""Leitura do plano de execucao em XML (SET STATISTICS XML ON / SHOWPLAN_XML).

Extrai so o que o framework usa: atributos da instrucao (QueryHash, QueryPlanHash, custo,
nivel de otimizacao, CE), tempos reais (QueryTimeStats), e por operador os contadores reais
somados entre threads (ActualLogicalReads etc.). Tambem calcula um hash proprio e estavel
do formato do plano (operadores + objetos, sem estimativas nem constantes).
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field

NS = "{http://schemas.microsoft.com/sqlserver/2004/07/showplan}"


def _i(v: str | None) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except ValueError:
        try:
            return int(float(v))
        except ValueError:
            return None


def _f(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


@dataclass
class Operador:
    node_id: int
    physical_op: str
    logical_op: str
    objeto: str | None = None          # schema.tabela.indice
    tipo_indice: str | None = None      # Clustered / NonClustered / Heap
    est_rows: float | None = None
    est_rows_read: float | None = None
    est_subtree_cost: float | None = None
    table_cardinality: float | None = None
    tem_contadores_reais: bool = False
    actual_rows: int = 0
    actual_rows_read: int = 0
    actual_executions: int = 0
    actual_scans: int = 0
    actual_logical_reads: int = 0
    actual_physical_reads: int = 0
    actual_read_aheads: int = 0
    actual_lob_logical_reads: int = 0
    actual_cpu_ms: int = 0
    actual_elapsed_ms: int = 0         # maximo entre threads (paralelismo)
    predicado: str | None = None
    lookup: bool = False


@dataclass
class Instrucao:
    texto: str
    tipo: str | None
    query_hash: str | None
    query_plan_hash: str | None
    custo_subarvore: float | None
    est_rows: float | None
    nivel_otimizacao: str | None
    versao_ce: int | None
    grau_paralelismo: int | None
    compile_time_ms: int | None
    compile_cpu_ms: int | None
    cpu_ms: int | None           # QueryTimeStats (so no plano real)
    elapsed_ms: int | None
    recuperado_do_cache: bool | None
    parametrizacao: str | None
    parametros: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    indices_ausentes: list[dict] = field(default_factory=list)
    operadores: list[Operador] = field(default_factory=list)

    @property
    def eh_plano_real(self) -> bool:
        return any(o.tem_contadores_reais for o in self.operadores)

    @property
    def leituras_logicas_plano(self) -> int:
        """Soma de ActualLogicalReads de todos os operadores (canal B). Exclui LOB."""
        return sum(o.actual_logical_reads for o in self.operadores)

    @property
    def hash_formato(self) -> str:
        """Hash proprio: operadores e objetos em ordem de documento; ignora estimativas, constantes e predicados."""
        assinatura = "\n".join(f"{o.physical_op}|{o.logical_op}|{o.objeto or ''}" for o in self.operadores)
        return hashlib.sha256(assinatura.encode("utf-8")).hexdigest()[:16]


@dataclass
class PlanoAnalisado:
    versao: str | None
    build: str | None
    instrucoes: list[Instrucao]

    def principal(self) -> Instrucao | None:
        """Primeira instrucao com operadores (a consulta medida, em lote de uma instrucao)."""
        for ins in self.instrucoes:
            if ins.operadores:
                return ins
        return self.instrucoes[0] if self.instrucoes else None

    def para_json(self) -> str:
        return json.dumps(
            {"versao": self.versao, "build": self.build, "instrucoes": [asdict(i) for i in self.instrucoes]},
            ensure_ascii=False,
        )


def _objeto(elem: ET.Element) -> tuple[str | None, str | None]:
    for obj in elem.iter(NS + "Object"):
        partes = [obj.get("Schema"), obj.get("Table"), obj.get("Index")]
        nome = ".".join(p for p in partes if p)
        return nome or None, obj.get("IndexKind")
    return None, None


def _operador(relop: ET.Element) -> Operador:
    op = Operador(
        node_id=_i(relop.get("NodeId")) or 0,
        physical_op=relop.get("PhysicalOp", ""),
        logical_op=relop.get("LogicalOp", ""),
        est_rows=_f(relop.get("EstimateRows")),
        est_rows_read=_f(relop.get("EstimatedRowsRead")),
        est_subtree_cost=_f(relop.get("EstimatedTotalSubtreeCost")),
        table_cardinality=_f(relop.get("TableCardinality")),
    )
    # filhos diretos do RelOp que descrevem o proprio operador (nao desce em RelOp filhos)
    for filho in relop:
        if filho.tag == NS + "RunTimeInformation":
            for c in filho.findall(NS + "RunTimeCountersPerThread"):
                op.tem_contadores_reais = True
                op.actual_rows += _i(c.get("ActualRows")) or 0
                op.actual_rows_read += _i(c.get("ActualRowsRead")) or 0
                op.actual_executions += _i(c.get("ActualExecutions")) or 0
                op.actual_scans += _i(c.get("ActualScans")) or 0
                op.actual_logical_reads += _i(c.get("ActualLogicalReads")) or 0
                op.actual_physical_reads += _i(c.get("ActualPhysicalReads")) or 0
                op.actual_read_aheads += _i(c.get("ActualReadAheads")) or 0
                op.actual_lob_logical_reads += _i(c.get("ActualLobLogicalReads")) or 0
                op.actual_cpu_ms += _i(c.get("ActualCPUms")) or 0
                op.actual_elapsed_ms = max(op.actual_elapsed_ms, _i(c.get("ActualElapsedms")) or 0)
        elif filho.tag not in (NS + "OutputList", NS + "Warnings", NS + "MemoryFractions"):
            # corpo do operador (IndexScan, NestedLoops, StreamAggregate...): objeto e predicado proprios
            for sub in filho:
                if sub.tag == NS + "Object" and op.objeto is None:
                    partes = [sub.get("Schema"), sub.get("Table"), sub.get("Index")]
                    op.objeto = ".".join(p for p in partes if p) or None
                    op.tipo_indice = sub.get("IndexKind")
                elif sub.tag == NS + "Predicate" and op.predicado is None:
                    so = sub.find(NS + "ScalarOperator")
                    op.predicado = so.get("ScalarString") if so is not None else None
            if filho.get("Lookup") in ("1", "true"):
                op.lookup = True
    if op.physical_op in ("Key Lookup", "RID Lookup"):
        op.lookup = True
    return op


def analisar_plano(xml: str) -> PlanoAnalisado:
    raiz = ET.fromstring(xml)
    instrucoes: list[Instrucao] = []
    for st in raiz.iter(NS + "StmtSimple"):
        qp = st.find(NS + "QueryPlan")
        ins = Instrucao(
            texto=st.get("StatementText", ""),
            tipo=st.get("StatementType"),
            query_hash=st.get("QueryHash"),
            query_plan_hash=st.get("QueryPlanHash"),
            custo_subarvore=_f(st.get("StatementSubTreeCost")),
            est_rows=_f(st.get("StatementEstRows")),
            nivel_otimizacao=st.get("StatementOptmLevel"),
            versao_ce=_i(st.get("CardinalityEstimationModelVersion")),
            grau_paralelismo=_i(qp.get("DegreeOfParallelism")) if qp is not None else None,
            compile_time_ms=_i(qp.get("CompileTime")) if qp is not None else None,
            compile_cpu_ms=_i(qp.get("CompileCPU")) if qp is not None else None,
            cpu_ms=None,
            elapsed_ms=None,
            recuperado_do_cache={"true": True, "false": False}.get(st.get("RetrievedFromCache", "").lower()),
            parametrizacao=st.get("StatementParameterizationType"),
        )
        if qp is not None:
            qts = qp.find(NS + "QueryTimeStats")
            if qts is not None:
                ins.cpu_ms = _i(qts.get("CpuTime"))
                ins.elapsed_ms = _i(qts.get("ElapsedTime"))
            for cr in qp.iter(NS + "ColumnReference"):
                if cr.get("ParameterCompiledValue") is not None or cr.get("ParameterRuntimeValue") is not None:
                    ins.parametros.append(
                        {
                            "nome": cr.get("Column"),
                            "tipo": cr.get("ParameterDataType"),
                            "valor_compilacao": cr.get("ParameterCompiledValue"),
                            "valor_execucao": cr.get("ParameterRuntimeValue"),
                        }
                    )
            for w in qp.iter(NS + "Warnings"):
                for filho in w:
                    ins.avisos.append(filho.tag.replace(NS, ""))
            for mig in qp.iter(NS + "MissingIndexGroup"):
                for mi in mig.iter(NS + "MissingIndex"):
                    cols = {
                        cg.get("Usage"): [c.get("Name") for c in cg.findall(NS + "Column")]
                        for cg in mi.findall(NS + "ColumnGroup")
                    }
                    ins.indices_ausentes.append(
                        {
                            "impacto": _f(mig.get("Impact")),
                            "tabela": f"{mi.get('Schema')}.{mi.get('Table')}",
                            "colunas": cols,
                        }
                    )
            ins.operadores = [_operador(r) for r in qp.iter(NS + "RelOp")]
        instrucoes.append(ins)
    return PlanoAnalisado(versao=raiz.get("Version"), build=raiz.get("Build"), instrucoes=instrucoes)
