"""Trava global: um experimento por vez.

Medicoes so valem com a instancia exclusiva (relatorio 16, 6.2 item 7). Qualquer caminho "ao vivo" (diagnostico
em consulta colada, validacao antes x depois) adquire esta trava; se estiver ocupada, a API responde HTTP 409.
A trava e por processo; coletas externas (coletar.py) sao detectadas a parte (leitura.coletas_em_andamento).
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Iterator


class TravaOcupada(RuntimeError):
    def __init__(self, dono: str, desde: float):
        super().__init__(f"outro experimento está em andamento ({dono})")
        self.dono = dono
        self.desde = desde


class Trava:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dono: str | None = None
        self._desde: float | None = None

    def tentar(self, dono: str) -> bool:
        if not self._lock.acquire(blocking=False):
            return False
        self._dono, self._desde = dono, time.time()
        return True

    def liberar(self) -> None:
        self._dono = self._desde = None
        try:
            self._lock.release()
        except RuntimeError:
            pass  # ja liberada

    @property
    def ocupada(self) -> bool:
        return self._lock.locked()

    def estado(self) -> dict:
        if not self.ocupada:
            return {"ocupada": False, "dono": None, "desde_utc": None, "ha_s": None}
        desde = self._desde or time.time()
        return {"ocupada": True, "dono": self._dono,
                "desde_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(desde)), "ha_s": round(time.time() - desde, 1)}

    @contextmanager
    def segurar(self, dono: str) -> Iterator[None]:
        if not self.tentar(dono):
            raise TravaOcupada(self._dono or "desconhecido", self._desde or time.time())
        try:
            yield
        finally:
            self.liberar()
