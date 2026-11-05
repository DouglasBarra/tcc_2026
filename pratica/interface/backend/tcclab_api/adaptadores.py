"""Adaptadores para modulos do nucleo escritos por outras frentes: `tcclab.analise` e `tcclab.diagnostico`.

Regra: a API NAO reimplementa estatistica nem regras de diagnostico. Se o modulo existe, delega a ele; se nao
existe (ou nao importa), devolve um estado "indisponivel" com o motivo, e a interface mostra isso, sem inventar dado.
`tcclab.stats.resumir` (estatistica descritiva) e sempre disponivel.
"""
from __future__ import annotations

import importlib
import inspect
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from tcclab.stats import resumir

from .leitura import RodadaRef


# ---------------------------------------------------------------------------------------- descritiva (sempre disponivel)
def descritiva(valores: list[float]) -> dict:
    """Estatistica descritiva de tcclab.stats.resumir (sem IC): n, minimo, q1, mediana, q3, maximo, media, dp, cv, atipicos."""
    return resumir(valores).como_dict()


# ---------------------------------------------------------------------------------------- tcclab.analise
def importar_analise() -> tuple[Any | None, str | None]:
    """(modulo, None) ou (None, motivo). O modulo exige numpy e scipy (extra opcional `analise` do nucleo)."""
    try:
        return importlib.import_module("tcclab.analise"), None
    except ModuleNotFoundError as e:
        if e.name in ("tcclab.analise",):
            return None, "módulo tcclab.analise ainda não disponível"
        return None, f"tcclab.analise exige o pacote '{e.name}', que não está instalado neste ambiente"
    except Exception as e:  # noqa: BLE001 - qualquer falha de import vira estado "indisponivel" com motivo
        return None, f"tcclab.analise não pôde ser importado: {e!r}"


class GerenteAnalise:
    """Executa `tcclab.analise.analisar` sobre o SQLite (somente leitura) e guarda o resultado em memoria.

    Chave do cache: arquivo + mtime + tamanho + experimentos + criterios. Se a coleta ainda escreve no SQLite,
    a chave muda e a analise e refeita (os dados parciais ficam sinalizados pelo status do experimento).
    A analise entre consultas (todos os experimentos) pode demorar (bootstrap); roda em segundo plano.
    """

    def __init__(self, modulo: Any | None, motivo: str | None):
        self.mod = modulo
        self.motivo = motivo
        self._cache: dict[tuple, dict] = {}
        self._estado: dict[tuple, dict] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analise")

    @property
    def disponivel(self) -> bool:
        return self.mod is not None

    def criterios(self) -> dict | None:
        return self.mod.Criterios().como_dict() if self.mod else None

    @staticmethod
    def _chave(ref: RodadaRef, exps: list[int], extra: str = "") -> tuple:
        """Impressao digital dos DADOS dos experimentos pedidos (nao do arquivo inteiro): escritas de outras consultas, feitas por
        uma coleta em andamento, nao invalidam o cache. Muda quando entram runs ou medicoes nesses experimentos."""
        ph = ",".join("?" for _ in exps)
        uri = ref.sqlite.resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            n_med, ult, concl, n_runs = con.execute(
                f"""SELECT COUNT(m.measurement_id), COALESCE(MAX(m.measurement_id), 0), COALESCE(SUM(r.status = 'concluido'), 0), COUNT(DISTINCT r.run_id)
                    FROM run r LEFT JOIN measurement m ON m.run_id = r.run_id WHERE r.experiment_id IN ({ph})""", exps).fetchone()
            st = tuple(r[0] for r in con.execute(f"SELECT status FROM experiment WHERE experiment_id IN ({ph}) ORDER BY experiment_id", exps))
        finally:
            con.close()
        return (str(ref.sqlite.resolve()), tuple(exps), n_med, ult, concl, n_runs, st, extra)

    def _calcular(self, ref: RodadaRef, exps: list[int], antipadroes: dict[str, str], papeis: dict[str, str] | None = None) -> dict:
        """Chama tcclab.analise.analisar. `papeis` (principal|controle) so e enviado se a funcao o aceitar: o modulo e
        escrito por outra frente e a assinatura pode variar; papeis fora dos aceitos (ex.: 'limpa') nao sao enviados."""
        kw: dict = {"antipadroes": antipadroes}
        if papeis and "papeis" in inspect.signature(self.mod.analisar).parameters:
            validos = getattr(self.mod, "PAPEIS", ("principal", "controle"))
            kw["papeis"] = {q: p for q, p in papeis.items() if p in validos}
        a = self.mod.analisar(ref.sqlite, exps, **kw)
        return self.mod.json_seguro(a.como_dict())

    def por_experimento(self, ref: RodadaRef, experiment_id: int, antipadroes: dict[str, str],
                        papeis: dict[str, str] | None = None) -> tuple[dict | None, str | None]:
        """(resultado, None) ou (None, motivo). Sincrono: um experimento (uma consulta) e rapido."""
        if not self.mod:
            return None, self.motivo
        chave = self._chave(ref, [experiment_id])
        with self._lock:
            if chave in self._cache:
                return self._cache[chave], None
        try:
            r = self._calcular(ref, [experiment_id], antipadroes, papeis)
        except Exception as e:  # noqa: BLE001 - ex.: experimento sem medicoes concluidas ou descartado por deriva
            return None, f"tcclab.analise não produziu resultado para o experimento {experiment_id}: {e}"
        with self._lock:
            self._cache[chave] = r
        return r, None

    def agregada(self, ref: RodadaRef, exps: list[int], antipadroes: dict[str, str], papeis: dict[str, str] | None = None,
                 rotulo: str = "", calcular: bool = False) -> dict:
        """Analise de varios experimentos (entre consultas). Nao bloqueia: devolve estado 'calculando' ate ficar pronta.
        So INICIA o calculo (bootstrap, usa CPU) se `calcular=True`: ler a tela nunca perturba uma coleta em andamento."""
        if not self.mod:
            return {"estado": "indisponivel", "motivo": self.motivo}
        if not exps:
            return {"estado": "indisponivel", "motivo": "nenhum experimento concluído para analisar"}
        chave = self._chave(ref, exps, "agregada:" + rotulo)
        with self._lock:
            if chave in self._cache:
                return {"estado": "pronto", "resultado": self._cache[chave], "calculada_em": self._estado.get(chave, {}).get("fim")}
            est = self._estado.get(chave)
            if est and est["estado"] == "erro":
                return {"estado": "erro", "motivo": est["motivo"]}
            if not est and not calcular:
                return {"estado": "nao_calculada", "n_experimentos": len(exps)}
            if not est:
                self._estado[chave] = {"estado": "calculando", "inicio": time.time()}
                self._pool.submit(self._rodar, chave, ref, exps, antipadroes, papeis)
            return {"estado": "calculando", "desde_s": round(time.time() - self._estado[chave]["inicio"], 1)}

    def _rodar(self, chave: tuple, ref: RodadaRef, exps: list[int], antipadroes: dict[str, str], papeis: dict[str, str] | None) -> None:
        try:
            r = self._calcular(ref, exps, antipadroes, papeis)
            with self._lock:
                self._cache[chave] = r
                self._estado[chave] = {"estado": "pronto", "fim": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())}
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._estado[chave] = {"estado": "erro", "motivo": str(e)}


# ---------------------------------------------------------------------------------------- tcclab.diagnostico
def importar_diagnostico() -> tuple[Any | None, str | None]:
    try:
        return importlib.import_module("tcclab.diagnostico"), None
    except ModuleNotFoundError as e:
        if e.name == "tcclab.diagnostico":
            return None, "módulo de diagnóstico ainda não disponível"
        return None, f"tcclab.diagnostico exige o pacote '{e.name}', que não está instalado neste ambiente"
    except Exception as e:  # noqa: BLE001
        return None, f"tcclab.diagnostico não pôde ser importado: {e!r}"


def _para_json(o: Any) -> Any:
    if is_dataclass(o) and not isinstance(o, type):
        return _para_json(asdict(o))
    if isinstance(o, dict):
        return {str(k): _para_json(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [_para_json(v) for v in o]
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    if hasattr(o, "value") and hasattr(o, "name") and not isinstance(o, (str, int, float, bool)):  # Enum
        return o.value
    return o


class AdaptadorDiagnostico:
    """Envolve `tcclab.diagnostico`. O contrato esperado do modulo e uma funcao
    `diagnosticar(sql, plano_xml=None)` que devolve um dict/dataclass/lista de evidencias (JSON-serializavel
    depois de `_para_json`). Se o modulo existir com outra assinatura, o erro e devolvido como estado, nao inventado."""

    NOMES_FUNCAO = ("diagnosticar", "diagnostico", "diagnose")

    def __init__(self, modulo: Any | None, motivo: str | None):
        self.mod = modulo
        self.motivo = motivo

    @property
    def disponivel(self) -> bool:
        return self.mod is not None

    def executar(self, sql: str, plano_xml: str | None = None) -> dict:
        if not self.mod:
            return {"disponivel": False, "motivo": self.motivo}
        fn = next((getattr(self.mod, n) for n in self.NOMES_FUNCAO if callable(getattr(self.mod, n, None))), None)
        if fn is None:
            return {"disponivel": False, "motivo": "tcclab.diagnostico existe, mas não expõe uma função diagnosticar(sql, plano_xml)"}
        try:
            try:
                r = fn(sql, plano_xml=plano_xml)
            except TypeError:
                r = fn(sql)
        except Exception as e:  # noqa: BLE001
            return {"disponivel": True, "erro": f"tcclab.diagnostico falhou: {e!r}", "resultado": None}
        return {"disponivel": True, "erro": None, "resultado": _para_json(r)}


def carregar_adaptadores() -> tuple[GerenteAnalise, AdaptadorDiagnostico]:
    return GerenteAnalise(*importar_analise()), AdaptadorDiagnostico(*importar_diagnostico())
