"""Estatistica descritiva por (variante, regime, metrica). Outliers sao SINALIZADOS, nunca descartados.

Regra de Tukey (pre-registro do relatorio 16, 6.4): fora de [Q1 - 1,5*IQR, Q3 + 1,5*IQR].
O numero de repeticoes e a regra definitiva de descarte ficam para depois do M0.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass


def _quantis(xs: list[float]) -> tuple[float, float, float]:
    if len(xs) == 1:
        return xs[0], xs[0], xs[0]
    q = statistics.quantiles(xs, n=4, method="inclusive")
    return q[0], q[1], q[2]


@dataclass
class Resumo:
    n: int
    minimo: float
    q1: float
    mediana: float
    q3: float
    maximo: float
    media: float
    dp: float
    cv: float | None
    atipicos: list[int]  # indices (posicao na lista original) fora da cerca de Tukey

    def como_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("n", "minimo", "q1", "mediana", "q3", "maximo", "media", "dp", "cv")} | {
            "atipicos": len(self.atipicos)
        }


def resumir(valores: list[float]) -> Resumo:
    xs = sorted(valores)
    q1, med, q3 = _quantis(xs)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    atip = [i for i, v in enumerate(valores) if v < lo or v > hi]
    media = statistics.fmean(xs)
    dp = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return Resumo(len(xs), xs[0], q1, med, q3, xs[-1], media, dp, (dp / media) if media else None, atip)
