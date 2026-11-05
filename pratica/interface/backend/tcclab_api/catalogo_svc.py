"""Catalogo de consultas (catalogo.toml + controles_limpos.toml) via tcclab.catalogo.

As descricoes das classes de antipadrao (AP1..AP4) vem dos comentarios do cabecalho do proprio TOML
("#   AP1 funcao sobre coluna filtrada (...)"), para nao embutir texto na interface.
"""
from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path

from tcclab import catalogo as cat

from .config import Configuracao

_RE_AP = re.compile(r"^#\s+(AP\d+)\s+(.+?)\s*$")


class CatalogoIndisponivel(RuntimeError):
    pass


def descricoes_antipadrao(caminho: Path) -> dict[str, str]:
    saida: dict[str, str] = {}
    if not caminho.is_file():
        return saida
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if linha.strip() and not linha.startswith("#"):
            break  # termina no primeiro item que nao e comentario
        m = _RE_AP.match(linha)
        if m:
            saida[m.group(1)] = m.group(2)
    return saida


def legenda_seletividade(caminho: Path) -> str | None:
    """Linha '# Seletividade: alta (< 1% ...)' do cabecalho do catalogo (definicao das tres classes)."""
    if not caminho.is_file():
        return None
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if linha.strip() and not linha.startswith("#"):
            break
        if linha.startswith("# Seletividade:"):
            return linha[len("# Seletividade:"):].strip()
    return None


def carregar_todos(cfg: Configuracao) -> tuple[list[tuple[cat.Consulta, str]], list[dict], dict[str, str]]:
    """([(consulta, nome_do_arquivo)], fontes, descricoes). Controles limpos so entram se o arquivo existir."""
    if not cfg.catalogo.is_file():
        raise CatalogoIndisponivel(f"catalogo nao encontrado: {cfg.catalogo}")
    itens: list[tuple[cat.Consulta, str]] = []
    fontes: list[dict] = []
    for caminho in (cfg.catalogo, cfg.controles):
        if not caminho.is_file():
            continue
        c = cat.carregar(caminho)
        fontes.append({"arquivo": caminho.name, "versao": c.versao, "banco": c.banco, "n_consultas": len(c.consultas)})
        itens += [(q, caminho.name) for q in c.consultas]
    ids = [q.id for q, _ in itens]
    if len(ids) != len(set(ids)):
        raise CatalogoIndisponivel("ids repetidos entre catalogo.toml e controles_limpos.toml")
    return itens, fontes, descricoes_antipadrao(cfg.catalogo)


def _extra(e: cat.Extra) -> dict:
    return asdict(e)


def consulta_dict(q: cat.Consulta, arquivo: str) -> dict:
    """Campos do catalogo, como escritos pelo autor (nada calculado alem de flags booleanas)."""
    return {
        "id": q.id, "antipadrao": q.antipadrao, "titulo": q.titulo, "tabelas": q.tabelas, "seletividade": q.seletividade,
        "papel": q.papel, "arquivo": arquivo,
        "tem_reescrita": bool(q.sql_reescrita), "tem_indice": bool(q.indice), "n_extras": len(q.extras),
    }


def consulta_detalhe(q: cat.Consulta, arquivo: str, nativo_ddl: str | None = None) -> dict:
    d = consulta_dict(q, arquivo) | {
        "sql_original": q.sql_original, "sql_reescrita": q.sql_reescrita, "sql_equivalencia": q.sql_equivalencia,
        "indice": q.indice, "indice_original": q.indice_original, "extras": [_extra(e) for e in q.extras],
    }
    try:
        vs, notas = cat.variantes_de(q, nativo_ddl)
        d["variantes_planejadas"] = [
            {"nome": v.nome, "kind": v.kind, "sql": v.sql, "ddl_aplicar": v.ddl_aplicar, "ddl_reverter": v.ddl_reverter}
            for v in vs
        ]
        d["notas_variantes"] = notas
    except ValueError as e:  # DDL de indice fora do padrao reconhecido por catalogo.ddl_reverter
        d["variantes_planejadas"] = []
        d["notas_variantes"] = [f"variantes nao montadas: {e}"]
    return d
