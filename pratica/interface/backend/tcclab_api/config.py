"""Configuracao da API: caminhos (derivados da posicao deste arquivo) e chaves de seguranca.

Variaveis de ambiente (todas opcionais):
  TCCLAB_RESULTADOS  pasta com <rodada>/tcclab.sqlite (padrao: pratica/experimento/resultados)
  TCCLAB_CATALOGO    catalogo.toml (padrao: pratica/experimento/catalogo.toml)
  TCCLAB_CONTROLES   controles_limpos.toml (padrao: pratica/experimento/controles_limpos.toml)
  TCCLAB_PLANOS      pasta de planos salvos (padrao: pratica/experimento/planos)
  TCCLAB_PLANOS_CONTROLES  planos das consultas de controle limpo (padrao: pratica/experimento/planos_controles)
  TCCLAB_AO_VIVO     "1" habilita os endpoints que falam com o SQL Server (padrao: DESLIGADO)
  TCCLAB_BANCO_AO_VIVO  banco alvo padrao dos endpoints ao vivo (padrao: o do catalogo)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

INTERFACE = Path(__file__).resolve().parents[2]          # pratica/interface
PRATICA = INTERFACE.parent                                # pratica


@dataclass(frozen=True)
class Configuracao:
    resultados: Path
    catalogo: Path
    controles: Path
    planos: Path
    planos_controles: Path
    ao_vivo_dir: Path          # onde as validacoes ao vivo gravam (NUNCA dentro de uma rodada de coleta)
    cache_dir: Path            # cache em disco dos resultados de tcclab.analise
    frontend_dist: Path | None
    ao_vivo_habilitado: bool = False
    banco_ao_vivo: str | None = None
    # uma coleta e considerada "em andamento" se algum experimento esta 'executando' e o arquivo mudou ha menos disso
    janela_coleta_ativa_s: float = 180.0
    reps_max_ao_vivo: int = 60
    aquecimentos_max_ao_vivo: int = 5

    @classmethod
    def de_ambiente(cls) -> "Configuracao":
        e = os.environ.get
        exp = PRATICA / "experimento"
        dist = INTERFACE / "frontend" / "dist"
        return cls(
            resultados=Path(e("TCCLAB_RESULTADOS", exp / "resultados")),
            catalogo=Path(e("TCCLAB_CATALOGO", exp / "catalogo.toml")),
            controles=Path(e("TCCLAB_CONTROLES", exp / "controles_limpos.toml")),
            planos=Path(e("TCCLAB_PLANOS", exp / "planos")),
            planos_controles=Path(e("TCCLAB_PLANOS_CONTROLES", exp / "planos_controles")),
            ao_vivo_dir=Path(e("TCCLAB_AO_VIVO_DIR", INTERFACE / "ao-vivo")),
            cache_dir=Path(e("TCCLAB_CACHE", INTERFACE / ".cache")),
            frontend_dist=dist if dist.is_dir() else None,
            ao_vivo_habilitado=e("TCCLAB_AO_VIVO", "0") == "1",
            banco_ao_vivo=e("TCCLAB_BANCO_AO_VIVO") or None,
        )
