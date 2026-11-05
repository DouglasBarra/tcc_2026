"""Caminhos "ao vivo": diagnosticar uma consulta colada (plano estimado) e validar antes x depois no SQL Server.

Tudo passa pela interface `tcclab.driver.Conexao` (injetada por `fabrica_conexao`) e pela TRAVA global.
Por seguranca:
  * desligado por padrao (TCCLAB_AO_VIVO=1 liga); desligado -> HTTP 503;
  * recusa se a trava esta ocupada ou se outra coleta esta medindo (HTTP 409);
  * so SELECT/WITH de instrucao unica; DDL so `CREATE INDEX ... ON ... (...) [INCLUDE (...)]`;
  * banco alvo precisa estar na lista de permissao do nucleo (tcclab.config.Config.permitidos);
  * a validacao grava num SQLite PROPRIO (ao-vivo/<id>/), nunca numa rodada de coleta.
Desenvolvimento: testado SOMENTE com Conexao falsa (tests/fake_conexao.py).
"""
from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from tcclab import canais, catalogo, medir
from tcclab.config import Config
from tcclab.driver import Conexao
from tcclab.runner import PlanoExperimento, Variante, executar_experimento
from tcclab.store import Armazem

from . import leitura as L
from .adaptadores import AdaptadorDiagnostico
from .config import Configuracao
from .planos_svc import resumir_plano
from .trava import Trava, TravaOcupada

LIMITE_SQL = 20_000
_BLOQUEADAS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|EXEC|EXECUTE|GRANT|REVOKE|DENY|SHUTDOWN|DBCC|BACKUP|"
    r"RESTORE|INTO|OPENROWSET|OPENQUERY|OPENDATASOURCE|WAITFOR|KILL|RECONFIGURE)\b|\bxp_\w+|\bsp_\w+", re.IGNORECASE)
_DDL_INDICE = re.compile(
    r"^\s*CREATE\s+(?:NONCLUSTERED\s+)?INDEX\s+\w+\s+ON\s+[\w\[\]\.]+\s*\([\w\[\]\s,]+\)"
    r"(?:\s+INCLUDE\s*\([\w\[\]\s,]+\))?\s*;?\s*$", re.IGNORECASE)
_TABELA = re.compile(r"^[A-Za-z0-9_\[\]\.]{1,128}$")
_BANCO = re.compile(r"^[A-Za-z0-9_]{1,64}$")


class AoVivoDesabilitado(RuntimeError):
    pass


class ColetaEmAndamento(RuntimeError):
    def __init__(self, coletas: list[dict]):
        super().__init__("há uma coleta em andamento: " + ", ".join(f"{c['rodada']} (atualizada há {c['modificada_ha_s']:.0f} s)" for c in coletas))
        self.coletas = coletas


class EntradaInvalida(ValueError):
    pass


class BancoNaoPermitido(PermissionError):
    pass


def _sem_comentarios_e_literais(sql: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    s = re.sub(r"--[^\n]*", " ", s)
    return re.sub(r"'(?:[^']|'')*'", "''", s)


def validar_consulta(sql: str, campo: str = "sql") -> str:
    """Aceita so uma instrucao SELECT/WITH sem efeitos colaterais conhecidos. Devolve o SQL sem alterar."""
    if not isinstance(sql, str) or not sql.strip():
        raise EntradaInvalida(f"{campo}: texto vazio")
    if len(sql) > LIMITE_SQL:
        raise EntradaInvalida(f"{campo}: excede {LIMITE_SQL} caracteres")
    limpo = _sem_comentarios_e_literais(sql).strip()
    if not re.match(r"^(SELECT|WITH)\b", limpo, re.IGNORECASE):
        raise EntradaInvalida(f"{campo}: só consultas SELECT/WITH são aceitas")
    corpo = limpo.rstrip(";").strip()
    if ";" in corpo:
        raise EntradaInvalida(f"{campo}: apenas uma instrução por vez")
    achou = _BLOQUEADAS.search(corpo)
    if achou:
        raise EntradaInvalida(f"{campo}: construção não permitida no modo ao vivo ({achou.group(0).upper()})")
    return sql


def validar_ddl_indice(ddl: str) -> str:
    if not isinstance(ddl, str) or len(ddl) > 2000 or not _DDL_INDICE.match(ddl):
        raise EntradaInvalida("ddl_indice: só 'CREATE [NONCLUSTERED] INDEX nome ON tabela (colunas) [INCLUDE (colunas)]' é aceito")
    return ddl.strip()


@dataclass
class Trabalho:
    id: str
    tipo: str
    banco: str
    criado_utc: str
    estado: str = "executando"     # executando | concluido | erro | invalida_equivalencia
    log: list[str] = field(default_factory=list)
    erro: str | None = None
    rodada: str | None = None
    experiment_id: int | None = None
    fim_utc: str | None = None

    def como_dict(self) -> dict:
        return {"id": self.id, "tipo": self.tipo, "banco": self.banco, "estado": self.estado, "criado_utc": self.criado_utc,
                "fim_utc": self.fim_utc, "log": list(self.log), "erro": self.erro, "rodada": self.rodada,
                "experiment_id": self.experiment_id}


def agora_utc() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


class AoVivo:
    def __init__(self, cfg: Configuracao, trava: Trava, fabrica: Callable[[str], Conexao] | None,
                 cfg_nucleo: Callable[[], Config], catalogo_itens: Callable[[], list]):
        self.cfg = cfg
        self.trava = trava
        self.fabrica = fabrica
        self.cfg_nucleo = cfg_nucleo
        self.catalogo_itens = catalogo_itens
        self.trabalhos: dict[str, Trabalho] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ estado e salvaguardas
    def estado(self) -> dict:
        return {"habilitado": self.cfg.ao_vivo_habilitado and self.fabrica is not None,
                "motivo_desabilitado": None if self.cfg.ao_vivo_habilitado else
                "desligado por padrão para proteger medições em andamento; defina TCCLAB_AO_VIVO=1 para habilitar",
                "trava": self.trava.estado(), "coletas_em_andamento": L.coletas_em_andamento(self.cfg)}

    def _exigir_disponivel(self) -> None:
        if not self.cfg.ao_vivo_habilitado or self.fabrica is None:
            raise AoVivoDesabilitado("o modo ao vivo está desligado (defina TCCLAB_AO_VIVO=1 para habilitar)")
        coletas = L.coletas_em_andamento(self.cfg)
        if coletas:
            raise ColetaEmAndamento(coletas)

    def _banco(self, banco: str | None) -> str:
        alvo = banco or self.cfg.banco_ao_vivo or self._banco_do_catalogo()
        if not _BANCO.match(alvo or ""):
            raise EntradaInvalida("nome de banco inválido")
        try:
            self.cfg_nucleo().exigir_banco_permitido(alvo)
        except PermissionError as e:
            raise BancoNaoPermitido(str(e)) from e
        return alvo

    def _banco_do_catalogo(self) -> str:
        try:
            from tcclab import catalogo as cat
            return cat.carregar(self.cfg.catalogo).banco
        except Exception:  # noqa: BLE001
            return "tcc_lab"

    # ------------------------------------------------------------------ diagnostico (plano estimado: nao executa a consulta)
    def diagnosticar(self, sql: str, banco: str | None, diag: AdaptadorDiagnostico) -> dict:
        self._exigir_disponivel()
        validar_consulta(sql)
        alvo = self._banco(banco)
        if not self.trava.tentar(f"diagnóstico em {alvo}"):
            raise TravaOcupada(self.trava.estado().get("dono") or "desconhecido", time.time())
        try:
            con = self.fabrica(alvo)
            try:
                xml, _ = canais.capturar_plano_estimado(con, sql)
            finally:
                con.fechar()
        finally:
            self.trava.liberar()
        resumo = resumir_plano(xml)
        return {"origem": "ao_vivo", "banco": alvo, "tipo_plano": "estimado", "plano_xml": xml, "plano": resumo,
                "diagnostico": diag.executar(sql, xml), "capturado_utc": agora_utc()}

    # ------------------------------------------------------------------ validacao antes x depois
    def validar(self, corpo: dict) -> Trabalho:
        self._exigir_disponivel()
        sql_antes = validar_consulta(corpo.get("sql_antes", ""), "sql_antes")
        sql_depois = validar_consulta(corpo["sql_depois"], "sql_depois") if corpo.get("sql_depois") else None
        ddl = validar_ddl_indice(corpo["ddl_indice"]) if corpo.get("ddl_indice") else None
        if not sql_depois and not ddl:
            raise EntradaInvalida("informe sql_depois, ddl_indice ou ambos")
        alvo = self._banco(corpo.get("banco"))
        regimes = tuple(corpo.get("regimes") or ("quente",))
        if not regimes or any(r not in ("quente", "frio") for r in regimes):
            raise EntradaInvalida("regimes deve conter 'quente' e/ou 'frio'")
        reps = corpo.get("repeticoes") or {}
        n_q, n_f = int(reps.get("quente", 10)), int(reps.get("frio", 5))
        aquec = int(corpo.get("aquecimentos", 3))
        if not (1 <= n_q <= self.cfg.reps_max_ao_vivo and 1 <= n_f <= self.cfg.reps_max_ao_vivo):
            raise EntradaInvalida(f"repetições devem estar entre 1 e {self.cfg.reps_max_ao_vivo}")
        if not (0 <= aquec <= self.cfg.aquecimentos_max_ao_vivo):
            raise EntradaInvalida(f"aquecimentos devem estar entre 0 e {self.cfg.aquecimentos_max_ao_vivo}")
        tabelas = list(corpo.get("tabelas") or [])
        query_id = corpo.get("query_id")
        if query_id:
            achada = next((q for q, _ in self.catalogo_itens() if q.id == query_id), None)
            if not achada:
                raise EntradaInvalida(f"query_id '{query_id}' não está no catálogo")
            tabelas = tabelas or list(achada.tabelas)
        if any(not _TABELA.match(t) for t in tabelas):
            raise EntradaInvalida("nome de tabela inválido")

        if not self.trava.tentar(f"validação ao vivo em {alvo}"):
            raise TravaOcupada(self.trava.estado().get("dono") or "desconhecido", time.time())
        job = Trabalho(id=time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:6], tipo="validacao",
                       banco=alvo, criado_utc=agora_utc())
        job.rodada = L.PREFIXO_AO_VIVO + job.id
        with self._lock:
            self.trabalhos[job.id] = job
            for velho in sorted(self.trabalhos)[:-30]:
                self.trabalhos.pop(velho, None)
        args = dict(sql_antes=sql_antes, sql_depois=sql_depois, ddl=ddl, regimes=regimes,
                    repeticoes={"quente": n_q, "frio": n_f}, aquecimentos=aquec, tabelas=tuple(tabelas),
                    query_id=query_id or "ADHOC", titulo=corpo.get("titulo") or "validação ao vivo",
                    semente=int(corpo.get("semente", 20261025)))
        threading.Thread(target=self._executar, args=(job, args), name=f"ao-vivo-{job.id}", daemon=True).start()
        return job

    def _executar(self, job: Trabalho, a: dict) -> None:
        pasta = self.cfg.ao_vivo_dir / job.id
        con = arm = None
        try:
            con = self.fabrica(job.banco)
            medir.preparar_sessao(con)
            if a["sql_depois"]:
                job.log.append("verificando equivalência de resultado entre antes e depois")
                q = catalogo.Consulta(id=a["query_id"], antipadrao="", titulo=a["titulo"], tabelas=list(a["tabelas"]),
                                      seletividade="", sql_original=a["sql_antes"], sql_reescrita=a["sql_depois"])
                eq = catalogo.verificar_equivalencia(con, q)
                if not eq["ok"]:
                    job.estado = "invalida_equivalencia"
                    job.erro = "o resultado de 'depois' difere do de 'antes': variante invalidada e não medida"
                    job.log.append(job.erro)
                    return
            ddl_rev = catalogo.ddl_reverter(a["ddl"]) if a["ddl"] else None
            kind = "prescricao" if (a["ddl"] and a["sql_depois"]) else ("indice" if a["ddl"] else "reescrita")
            variantes = [Variante("antes", a["sql_antes"], kind="original"),
                         Variante("depois", a["sql_depois"] or a["sql_antes"], a["ddl"], ddl_rev, kind=kind)]
            plano = PlanoExperimento(
                nome=f"ao-vivo:{job.id}", database=job.banco, variantes=variantes, regimes=a["regimes"],
                aquecimentos=a["aquecimentos"], repeticoes=a["repeticoes"], ordem="ABA", semente=a["semente"],
                cache_planos="escopo", estatisticas_tabelas=a["tabelas"], consulta_id=a["query_id"], titulo=a["titulo"])
            arm = Armazem(pasta / "tcclab.sqlite")
            res = executar_experimento(self.cfg_nucleo(), arm, plano, progresso=job.log.append, con=con)
            job.experiment_id = res["experiment_id"]
            job.estado = "concluido"
        except BaseException as e:  # noqa: BLE001 - o trabalho nunca pode vazar a trava
            job.estado = "erro"
            job.erro = f"{type(e).__name__}: {e}"
            job.log.append("ERRO: " + job.erro)
        finally:
            for obj in (arm, con):
                try:
                    if obj is not None:
                        obj.fechar()
                except Exception:  # noqa: BLE001
                    pass
            job.fim_utc = agora_utc()
            self.trava.liberar()

    def obter(self, job_id: str) -> Trabalho | None:
        with self._lock:
            return self.trabalhos.get(job_id)

    def listar(self) -> list[dict]:
        with self._lock:
            return [t.como_dict() for t in sorted(self.trabalhos.values(), key=lambda t: t.criado_utc, reverse=True)]
