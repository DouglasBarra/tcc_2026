"""Configuracao de conexao e lista de permissao de bancos.

A senha do SA nunca e impressa nem gravada em resultados. Ordem de busca:
variaveis de ambiente TCCLAB_* e depois o arquivo .env de pratica/infra
(TCCLAB_ENV_FILE sobrescreve o caminho).
"""
from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path

PADRAO_PERMITIDOS = ("tcc_lab", "m0_*", "so_exp*")  # so_exp*: clone de experimento; o banco base StackOverflow2010 nunca e medido


def _achar_env_file() -> Path | None:
    explicito = os.environ.get("TCCLAB_ENV_FILE")
    if explicito:
        return Path(explicito)
    aqui = Path(__file__).resolve()
    for pai in aqui.parents:
        cand = pai / "infra" / ".env"
        if cand.is_file():
            return cand
    return None


def _ler_env(caminho: Path | None) -> dict[str, str]:
    dados: dict[str, str] = {}
    if caminho and caminho.is_file():
        for linha in caminho.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, v = linha.split("=", 1)
            dados[k.strip()] = v.strip()
    return dados


@dataclass(frozen=True)
class Config:
    host: str = "127.0.0.1"
    port: int = 1433
    user: str = "sa"
    password: str = field(default="", repr=False)  # repr=False: nunca aparece em logs
    database: str = "tcc_lab"
    permitidos: tuple[str, ...] = PADRAO_PERMITIDOS
    login_timeout: float = 15.0
    query_timeout: float | None = 300.0

    @classmethod
    def carregar(cls, **sobrescritas) -> "Config":
        env = _ler_env(_achar_env_file())
        get = lambda nome, alt=None: os.environ.get(f"TCCLAB_{nome}", alt)  # noqa: E731
        permitidos = get("PERMITIDOS")
        base = dict(
            host=get("HOST", "127.0.0.1"),
            port=int(get("PORT", env.get("MSSQL_PORT", "1433"))),
            user=get("USER", "sa"),
            password=get("PASSWORD", env.get("MSSQL_SA_PASSWORD", "")),
            database=get("DATABASE", env.get("TCC_DB", "tcc_lab")),
            permitidos=tuple(p.strip() for p in permitidos.split(",")) if permitidos else PADRAO_PERMITIDOS,
        )
        base.update({k: v for k, v in sobrescritas.items() if v is not None})
        return cls(**base)

    def banco_permitido(self, nome: str) -> bool:
        return any(fnmatch.fnmatchcase(nome.lower(), p.lower()) for p in self.permitidos)

    def exigir_banco_permitido(self, nome: str) -> None:
        """Barreira de seguranca: comandos que alteram estado (DDL, DBCC, CHECKPOINT) so em banco permitido."""
        if not self.banco_permitido(nome):
            raise PermissionError(
                f"Banco '{nome}' fora da lista de permissao {self.permitidos}. "
                "Ajuste TCCLAB_PERMITIDOS se for intencional."
            )
