"""Aplicacao FastAPI do TCC Lab (somente leitura sobre os resultados; caminhos ao vivo atras de trava).

Executar: `uvicorn tcclab_api.main:app --host 127.0.0.1 --port 8000` (a partir de pratica/interface/backend).
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

import tcclab
from tcclab.config import Config as ConfigNucleo
from tcclab.driver import Conexao

from . import __version__
from . import leitura as L
from . import planos_svc as P
from . import servicos as S
from .adaptadores import GerenteAnalise, AdaptadorDiagnostico, carregar_adaptadores
from .ao_vivo import (AoVivo, AoVivoDesabilitado, BancoNaoPermitido, ColetaEmAndamento, EntradaInvalida)
from .catalogo_svc import CatalogoIndisponivel, carregar_todos, consulta_detalhe, consulta_dict, legenda_seletividade
from .config import Configuracao
from .trava import Trava, TravaOcupada

ID_CONSULTA_RE = r"^[A-Za-z][A-Za-z0-9_-]{0,20}$"
METRICAS_API = ("logical_reads", "physical_reads", "read_ahead_reads", "cpu_ms", "elapsed_ms", "cpu_us", "elapsed_us")


class CorpoDiagnostico(BaseModel):
    sql: str = Field(..., max_length=20_000)
    banco: str | None = None


class CorpoValidacao(BaseModel):
    sql_antes: str = Field(..., max_length=20_000)
    sql_depois: str | None = Field(None, max_length=20_000)
    ddl_indice: str | None = Field(None, max_length=2_000)
    banco: str | None = None
    query_id: str | None = Field(None, pattern=ID_CONSULTA_RE)
    tabelas: list[str] | None = None
    regimes: list[str] | None = None
    repeticoes: dict[str, int] | None = None
    aquecimentos: int = 3
    titulo: str | None = Field(None, max_length=200)
    semente: int = 20261025


class CorpoDiagnosticoSalvo(BaseModel):
    rotulo_plano: str | None = None


class _CatalogoCache:
    """Recarrega os TOML quando o arquivo muda (o autor edita o catalogo durante o projeto)."""

    def __init__(self, cfg: Configuracao):
        self.cfg = cfg
        self._chave = None
        self._valor = None
        self._lock = threading.Lock()

    def obter(self):
        chave = tuple((p, p.stat().st_mtime_ns if p.is_file() else None) for p in (self.cfg.catalogo, self.cfg.controles))
        with self._lock:
            if chave != self._chave:
                self._valor = carregar_todos(self.cfg)
                self._chave = chave
            return self._valor


def create_app(config: Configuracao | None = None, fabrica_conexao: Callable[[str], Conexao] | None = None,
               cfg_nucleo: ConfigNucleo | None = None, gerente: GerenteAnalise | None = None,
               diagnostico: AdaptadorDiagnostico | None = None) -> FastAPI:
    cfg = config or Configuracao.de_ambiente()
    g0, d0 = carregar_adaptadores()
    gerente = gerente or g0
    diag = diagnostico or d0
    trava = Trava()
    cat_cache = _CatalogoCache(cfg)
    _cfg_nucleo_pronto: dict = {}

    def get_cfg_nucleo() -> ConfigNucleo:
        # Config.carregar() le pratica/infra/.env (senha do SA); so e chamada em caminhos ao vivo, nunca na leitura.
        if cfg_nucleo is not None:
            return cfg_nucleo
        if "c" not in _cfg_nucleo_pronto:
            _cfg_nucleo_pronto["c"] = ConfigNucleo.carregar()
        return _cfg_nucleo_pronto["c"]

    fabrica = fabrica_conexao
    if fabrica is None and cfg.ao_vivo_habilitado:
        from tcclab.driver import conectar

        def fabrica(banco: str) -> Conexao:  # type: ignore[misc]
            return conectar(get_cfg_nucleo(), banco)

    ao_vivo = AoVivo(cfg, trava, fabrica, get_cfg_nucleo, lambda: cat_cache.obter()[0])

    app = FastAPI(title="TCC Lab", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.cfg, app.state.trava, app.state.ao_vivo, app.state.gerente, app.state.diag = cfg, trava, ao_vivo, gerente, diag

    # ------------------------------------------------------------------ erros
    def _erro(status: int, codigo: str, detalhe: str, **extra) -> JSONResponse:
        return JSONResponse(status_code=status, content={"codigo": codigo, "detalhe": detalhe, **extra})

    @app.exception_handler(StarletteHTTPException)
    async def _h_http(_: Request, e: StarletteHTTPException):
        return _erro(e.status_code, "erro_http", str(e.detail))

    @app.exception_handler(L.RodadaInexistente)
    async def _h_rodada(_: Request, e: L.RodadaInexistente):
        return _erro(404, "rodada_inexistente", f"rodada '{e}' não encontrada")

    @app.exception_handler(L.RodadaIncompativel)
    async def _h_incompativel(_: Request, e: L.RodadaIncompativel):
        return _erro(422, "rodada_incompativel", str(e))

    @app.exception_handler(sqlite3.DatabaseError)
    async def _h_sqlite(_: Request, e: sqlite3.DatabaseError):
        return _erro(503, "banco_ocupado", f"o SQLite está ocupado ou ilegível: {e}")

    @app.exception_handler(CatalogoIndisponivel)
    async def _h_catalogo(_: Request, e: CatalogoIndisponivel):
        return _erro(503, "catalogo_indisponivel", str(e))

    @app.exception_handler(TravaOcupada)
    async def _h_trava(_: Request, e: TravaOcupada):
        return _erro(409, "trava_ocupada", str(e), dono=e.dono)

    @app.exception_handler(ColetaEmAndamento)
    async def _h_coleta(_: Request, e: ColetaEmAndamento):
        return _erro(409, "coleta_em_andamento", str(e), coletas=e.coletas)

    @app.exception_handler(AoVivoDesabilitado)
    async def _h_desligado(_: Request, e: AoVivoDesabilitado):
        return _erro(503, "ao_vivo_desabilitado", str(e))

    @app.exception_handler(EntradaInvalida)
    async def _h_entrada(_: Request, e: EntradaInvalida):
        return _erro(422, "entrada_invalida", str(e))

    @app.exception_handler(BancoNaoPermitido)
    async def _h_banco(_: Request, e: BancoNaoPermitido):
        return _erro(403, "banco_nao_permitido", str(e))

    # ------------------------------------------------------------------ auxiliares
    def rodada(nome: str) -> L.RodadaRef:
        return L.achar_rodada(cfg, nome)

    def antipadroes() -> dict[str, str]:
        try:
            return {q.id: q.antipadrao for q, _ in cat_cache.obter()[0]}
        except CatalogoIndisponivel:
            return {}

    def papeis() -> dict[str, str]:
        try:
            return {q.id: q.papel for q, _ in cat_cache.obter()[0]}
        except CatalogoIndisponivel:
            return {}

    def exigir_consulta_id(qid: str) -> None:
        import re
        if not re.match(ID_CONSULTA_RE, qid):
            raise HTTPException(404, "consulta inexistente")

    # ------------------------------------------------------------------ estado
    @app.get("/api/estado")
    def estado():
        try:
            itens, fontes, _ = cat_cache.obter()
            cat = {"disponivel": True, "fontes": fontes, "n_consultas": len(itens)}
        except CatalogoIndisponivel as e:
            cat = {"disponivel": False, "motivo": str(e)}
        return {
            "api": __version__, "tcclab": tcclab.__version__,
            "resultados": str(cfg.resultados), "n_rodadas": len(L.descobrir_rodadas(cfg)),
            "catalogo": cat,
            "modulos": {
                "analise": {"disponivel": gerente.disponivel, "motivo": gerente.motivo},
                "diagnostico": {"disponivel": diag.disponivel, "motivo": diag.motivo},
            },
            "ao_vivo": ao_vivo.estado(),
        }

    # ------------------------------------------------------------------ rodadas
    @app.get("/api/rodadas")
    def rodadas():
        return {"rodadas": [L.resumo_rodada(cfg, r) for r in L.descobrir_rodadas(cfg)]}

    @app.get("/api/rodadas/{nome}")
    def rodada_detalhe(nome: str):
        ref = rodada(nome)
        resumo = L.resumo_rodada(cfg, ref)
        if not resumo["compativel"]:
            return resumo
        with L.abrir(ref) as con:
            coleta = []
            if L.tem_tabela(con, "coleta_consulta"):
                coleta = [dict(r) for r in con.execute("SELECT * FROM coleta_consulta ORDER BY rowid")]
                for c in coleta:
                    c["equivalencia"] = L._json(c.pop("equivalencia_json"), None)
                    c["notas"] = L._json(c.pop("notas_json"), [])
        return resumo | {"coleta_consultas": coleta}

    @app.get("/api/rodadas/{nome}/ambiente")
    def ambiente(nome: str):
        ref = rodada(nome)
        resumo = L.resumo_rodada(cfg, ref)
        with L.abrir(ref) as con:
            return {"rodada": nome, "ambientes": L.ambientes(con), "parametros_rodada": resumo.get("parametros_rodada")}

    @app.get("/api/rodadas/{nome}/runs")
    def runs(nome: str, query_id: str | None = None, variante: str | None = None, regime: str | None = None,
             experimento: int | None = None, limite: int = Query(300, ge=1, le=2000)):
        return S.listar_runs(rodada(nome), query_id, variante, regime, experimento, limite)

    @app.get("/api/rodadas/{nome}/analise")
    def analise_agregada(nome: str, calcular: bool = False):
        """Analise entre consultas (tcclab.analise) de todos os experimentos concluidos e nao descartados por deriva.
        Os conjuntos (principal, sem_atipicos, com_controles...) saem do proprio modulo; a UI escolhe qual mostrar."""
        ref = rodada(nome)
        with L.abrir(ref) as con:
            candidatos = []
            for r in con.execute(
                """SELECT DISTINCT e.experiment_id, v.query_id FROM experiment e JOIN run r ON r.experiment_id = e.experiment_id
                   JOIN variant v ON v.variant_id = r.variant_id WHERE e.status = 'concluido' ORDER BY e.experiment_id"""):
                exps = L.experimentos_da_consulta(con, r["query_id"])
                e = next((x for x in exps if x["experiment_id"] == r["experiment_id"]), None)
                if e and not e["descartado_por_deriva"]:
                    candidatos.append(r["experiment_id"])
        res = gerente.agregada(ref, sorted(set(candidatos)), antipadroes(), papeis(), calcular=calcular)
        if res["estado"] == "pronto":
            r = res.pop("resultado")
            res["resultado"] = {k: r.get(k) for k in ("meta", "criterios", "criterios_sha", "avisos", "agregados", "veredictos", "consultas", "descartados")}
        res["experimentos_analisados"] = sorted(set(candidatos))
        return res

    # ------------------------------------------------------------------ catalogo e consultas
    @app.get("/api/catalogo")
    def catalogo_listar():
        itens, fontes, descricoes = cat_cache.obter()
        medidas: dict[str, list[dict]] = {}
        for ref in L.descobrir_rodadas(cfg):
            try:
                with L.abrir(ref) as con:
                    for qid, exps in L.consultas_medidas(con).items():
                        medidas.setdefault(qid, []).append({"rodada": ref.nome, "tipo": ref.tipo, "experimentos": exps})
            except (L.RodadaIncompativel, sqlite3.DatabaseError):
                continue
        consultas = [consulta_dict(q, arq) | {"medida_em": medidas.get(q.id, [])} for q, arq in itens]
        return {"fontes": fontes, "descricoes_antipadrao": descricoes, "consultas": consultas,
                "legenda_seletividade": legenda_seletividade(cfg.catalogo)}

    @app.get("/api/consultas/{query_id}")
    def consulta(query_id: str, rodada_nome: str | None = Query(None, alias="rodada")):
        exigir_consulta_id(query_id)
        itens, _, descricoes = cat_cache.obter()
        achada = next(((q, arq) for q, arq in itens if q.id == query_id), None)
        if not achada:
            raise HTTPException(404, f"consulta '{query_id}' não está no catálogo")
        nativo = None
        medida_em = []
        for ref in L.descobrir_rodadas(cfg):
            try:
                with L.abrir(ref) as con:
                    exps = L.experimentos_da_consulta(con, query_id)
                    if exps:
                        medida_em.append({"rodada": ref.nome, "tipo": ref.tipo, "experimentos": exps})
                        if (rodada_nome is None or rodada_nome == ref.nome) and L.tem_tabela(con, "coleta_consulta"):
                            padrao = L.experimento_padrao(exps)
                            linha = L.linha_coleta(con, padrao["experiment_id"]) if padrao else None
                            if linha and linha.get("nativo_ddl"):
                                nativo = linha["nativo_ddl"]
            except (L.RodadaIncompativel, sqlite3.DatabaseError):
                continue
        q, arq = achada
        return consulta_detalhe(q, arq, nativo) | {"descricao_antipadrao": descricoes.get(q.antipadrao), "medida_em": medida_em,
                                                    "nativo_ddl": nativo, "planos_salvos": P.planos_salvos_da_consulta(cfg, query_id)}

    @app.get("/api/rodadas/{nome}/consultas/{query_id}/validacao")
    def validacao(nome: str, query_id: str, experimento: int | None = None):
        exigir_consulta_id(query_id)
        return S.montar_validacao(cfg, rodada(nome), query_id, experimento, gerente, antipadroes(), papeis())

    @app.get("/api/rodadas/{nome}/consultas/{query_id}/repeticoes")
    def repeticoes(nome: str, query_id: str, metrica: str, experimento: int | None = None):
        exigir_consulta_id(query_id)
        if metrica not in METRICAS_API:
            raise HTTPException(422, f"métrica inválida; use uma de {', '.join(METRICAS_API)}")
        ref = rodada(nome)
        with L.abrir(ref) as con:
            exps = L.experimentos_da_consulta(con, query_id)
            esc = next((e for e in exps if e["experiment_id"] == experimento), None) if experimento else L.experimento_padrao(exps)
            if esc is None:
                return {"rodada": nome, "query_id": query_id, "metrica": metrica, "disponivel": False,
                        "motivo": "consulta sem experimento nesta rodada", "pontos": []}
            if metrica not in L.metricas_validas(con):
                raise HTTPException(422, "métrica ausente no esquema desta rodada")
            pontos = L.repeticoes(con, esc["experiment_id"], metrica)
        return {"rodada": nome, "query_id": query_id, "metrica": metrica, "disponivel": True,
                "experiment_id": esc["experiment_id"], "pontos": pontos}

    @app.get("/api/rodadas/{nome}/consultas/{query_id}/escrita")
    def escrita(nome: str, query_id: str):
        exigir_consulta_id(query_id)
        return S.montar_escrita(rodada(nome), query_id, gerente)

    # ------------------------------------------------------------------ planos
    @app.get("/api/rodadas/{nome}/planos/{plan_id}")
    def plano_rodada(nome: str, plan_id: int):
        ref = rodada(nome)
        with L.abrir(ref) as con:
            linha = L.plano_por_id(con, plan_id)
        if not linha:
            raise HTTPException(404, f"plano {plan_id} não existe na rodada {nome}")
        xml, motivo = P.ler_plano_da_rodada(ref, linha)
        return {"rodada": nome, "plan_id": plan_id, "tipo_plano": linha["kind"], "query_hash": linha["query_hash"],
                "query_plan_hash": linha["query_plan_hash"], "custo_subarvore": linha["statement_cost"], "dop": linha["dop"],
                "runs": linha["runs"], "xml": xml, "motivo_sem_xml": motivo,
                "resumo": P.resumir_plano(xml) if xml else None, "origem": "plano gravado na coleta (tabela plan)"}

    @app.get("/api/consultas/{query_id}/planos-salvos")
    def planos_salvos(query_id: str):
        exigir_consulta_id(query_id)
        lista = P.planos_salvos_da_consulta(cfg, query_id)
        return {"query_id": query_id, "pasta": str(cfg.planos), "planos": lista,
                "disponivel": bool(lista),
                "motivo": None if lista else "nenhum plano salvo para esta consulta (rode experimento/salvar_planos.py)"}

    @app.get("/api/consultas/{query_id}/planos-salvos/{rotulo}")
    def plano_salvo(query_id: str, rotulo: str):
        exigir_consulta_id(query_id)
        xml, motivo = P.ler_plano_salvo(cfg, query_id, rotulo)
        if xml is None:
            raise HTTPException(404, motivo)
        return {"query_id": query_id, "rotulo": rotulo, "xml": xml, "resumo": P.resumir_plano(xml),
                "origem": f"experimento/planos/{query_id}/{rotulo}.sqlplan"}

    @app.post("/api/consultas/{query_id}/diagnostico")
    def diagnostico_offline(query_id: str, corpo: CorpoDiagnosticoSalvo):
        """Diagnostico da consulta do catalogo sobre o plano SALVO em disco (sem tocar no SQL Server)."""
        exigir_consulta_id(query_id)
        achada = next((q for q, _ in cat_cache.obter()[0] if q.id == query_id), None)
        if not achada:
            raise HTTPException(404, f"consulta '{query_id}' não está no catálogo")
        xml = None
        if corpo.rotulo_plano:
            xml, motivo = P.ler_plano_salvo(cfg, query_id, corpo.rotulo_plano)
            if xml is None:
                raise HTTPException(404, motivo)
        return {"origem": "catalogo", "query_id": query_id, "rotulo_plano": corpo.rotulo_plano,
                "diagnostico": diag.executar(achada.sql_original, xml)}

    # ------------------------------------------------------------------ ao vivo
    @app.get("/api/ao-vivo")
    def ao_vivo_estado():
        return ao_vivo.estado()

    @app.post("/api/ao-vivo/diagnosticar")
    def ao_vivo_diagnosticar(corpo: CorpoDiagnostico):
        return ao_vivo.diagnosticar(corpo.sql, corpo.banco, diag)

    @app.post("/api/ao-vivo/validar", status_code=202)
    def ao_vivo_validar(corpo: CorpoValidacao):
        job = ao_vivo.validar(corpo.model_dump())
        return job.como_dict()

    @app.get("/api/ao-vivo/trabalhos")
    def ao_vivo_trabalhos():
        return {"trabalhos": ao_vivo.listar()}

    @app.get("/api/ao-vivo/trabalhos/{job_id}")
    def ao_vivo_trabalho(job_id: str):
        job = ao_vivo.obter(job_id)
        if not job:
            raise HTTPException(404, "trabalho não encontrado")
        return job.como_dict()

    # ------------------------------------------------------------------ interface web (build do Vite)
    if cfg.frontend_dist is not None:
        app.mount("/", StaticFiles(directory=cfg.frontend_dist, html=True), name="web")

    return app


def app_padrao() -> FastAPI:
    return create_app()


app = app_padrao()  # `uvicorn tcclab_api.main:app`
