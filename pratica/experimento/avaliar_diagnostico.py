#!/usr/bin/env python
"""Validacao do instrumento (relatorio 16, secao 6.7, item 2): acerto da deteccao da camada 1 (Diagnostico).

Roda `tcclab.diagnostico.diagnosticar` sobre cada consulta de catalogo.toml (original e reescrita) e de
controles_limpos.toml, em tres modos: so SQL, SQL + plano estimado e SQL + plano real (os planos vem de
experimento/planos/<ID>/... para o catalogo e experimento/planos_controles/<ID>/... para os controles, gerados por
salvar_planos.py; modo sem arquivo e registrado como ausente, nunca inventado). Offline: nao conecta ao SQL Server.

Rotulos (a "verdade" desta avaliacao vem do catalogo, escrito antes do detector):
  - original de consulta do catalogo: o antipadrao declarado (AP1..AP4) deve ser detectado (positivo);
  - reescrita do catalogo: o antipadrao declarado nao deve mais aparecer (negativo para as regras daquele antipadrao);
  - controle limpo: nenhum achado com antipadrao (negativo para todas as regras);
  - achado de OUTRA classe numa consulta rotulada (ex.: varredura sem indice na reescrita de F1) nao e pontuado: vai para
    "achados adicionais", porque a consulta pode legitimamente ter mais de um problema.
Achado sem antipadrao (aviso informativo do plano) tambem nao e pontuado.

Saidas em experimento/resultados/diagnostico/: avaliacao_casos.csv, avaliacao_achados.csv, avaliacao_matriz_regras.csv,
avaliacao_matriz_antipadroes.csv e avaliacao_diagnostico.md.
Uso (em pratica/nucleo): .venv/bin/python ../experimento/avaliar_diagnostico.py [--planos DIR] [--planos-controles DIR] [--so-sql]
                                                              [--limiar p1_linhas_minimas=50000]
Conjunto de VALIDACAO (heldout.toml, com `esperado`/`tolerado`/`proibido`; PRE-REGISTRO.md secao 5): detectado pelo formato do arquivo;
  .venv/bin/python ../experimento/avaliar_diagnostico.py --catalogo ../experimento/heldout.toml --so-sql
roda UMA vez por modo (recusa regravar), grava em resultados/diagnostico_heldout/heldout_<execucao>_*.csv|md e confere os SHA-256 do
pre-registro. Planos esperados em experimento/planos_heldout/<ID>/original.{estimado,real}.sqlplan.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import math
import re
import sys
import tomllib
from dataclasses import dataclass, fields, replace
from datetime import date
from pathlib import Path

from tcclab import catalogo
from tcclab.diagnostico import REGRAS, Diagnostico, Limiares, diagnosticar

AQUI = Path(__file__).resolve().parent
MODOS = ("sql", "estimado", "real")
ROTULO_MODO = {"sql": "só SQL", "estimado": "SQL + plano estimado", "real": "SQL + plano real"}
SEM_PLANO = {"AP1", "AP2", "AP3"}   # antipadroes que alguma regra de SQL consegue enxergar (AP4 exige o plano)
APS = ("AP1", "AP2", "AP3", "AP4")


@dataclass
class Caso:
    consulta: str
    grupo: str           # catalogo | controle
    ap: str              # antipadrao declarado ("NENHUM" nos controles limpos)
    papel: str
    variante: str        # original | reescrita
    modo: str            # sql | estimado | real
    arquivo_plano: str | None
    diag: Diagnostico

    def com_ap(self) -> list:
        """Achados com antipadrao (os que contam)."""
        return [a for a in self.diag.achados if a.antipadrao]

    def disparou(self, regra: str) -> bool:
        return any(a.regra == regra for a in self.com_ap())

    @property
    def aps_detectados(self) -> set[str]:
        return {a.antipadrao for a in self.com_ap()}


# ------------------------------------------------------------------------------------------------ geracao dos casos
def gerar_casos(arquivo_catalogo: Path, arquivo_controles: Path, dir_planos: Path, dir_controles: Path, so_sql: bool, lim: Limiares):
    casos: list[Caso] = []
    ausentes: list[tuple[str, str, str]] = []
    for grupo, arquivo, pasta in (("catalogo", arquivo_catalogo, dir_planos), ("controle", arquivo_controles, dir_controles)):
        for c in catalogo.carregar(arquivo).consultas:
            for variante, sql in (("original", c.sql_original), ("reescrita", c.sql_reescrita)):
                if not sql:
                    continue
                for modo in MODOS:
                    xml, nome = None, None
                    if modo != "sql":
                        if so_sql:
                            continue
                        caminho = pasta / c.id / f"{variante}.{modo}.sqlplan"
                        if not caminho.exists():
                            ausentes.append((c.id, variante, modo))
                            continue
                        xml, nome = caminho.read_text(encoding="utf-8"), str(caminho.relative_to(pasta.parent))
                    casos.append(Caso(c.id, grupo, c.antipadrao if grupo == "catalogo" else "NENHUM", c.papel, variante, modo, nome,
                                      diagnosticar(sql, xml, lim)))
    return casos, ausentes


# ------------------------------------------------------------------------------------------------ rotulos e metricas
def rotulo(caso: Caso, regra: str) -> str | None:
    """'pos', 'neg' ou None (nao pontuado) para a regra neste caso."""
    ap_regra = REGRAS[regra].antipadrao
    if caso.grupo == "controle":
        return "neg"
    if caso.variante == "original":
        # P4 (PlanAffectingConvert) trata conversao de TIPO; o catalogo so tem funcoes explicitas (AP1), nenhuma consulta com tipos
        # incompativeis. Nao ha positivo rotulavel para P4: so os negativos (reescritas e controles) contam.
        return "pos" if caso.ap == ap_regra and regra != "P4" else None
    return "neg" if caso.ap == ap_regra else None


def resultado(caso: Caso) -> str:
    d = caso.diag
    if caso.grupo == "controle":
        return "falso_positivo" if caso.com_ap() else "controle_limpo"
    if caso.variante == "original":
        if caso.modo == "sql" and caso.ap not in SEM_PLANO:
            return "fora_do_alcance"
        nivel = d.nivel_do_antipadrao(caso.ap)
        return {"confirmado": "acerto_confirmado", "indicio": "acerto_indicio", None: "omissao"}[nivel]
    residual_sql = [a for a in caso.com_ap() if a.antipadrao == caso.ap and a.regra[0] == "S"]
    residual_plano = [a for a in caso.com_ap() if a.antipadrao == caso.ap and a.regra[0] == "P"]
    if residual_sql:
        return "reescrita_com_residuo_sql"
    return "reescrita_com_residuo_plano" if residual_plano else "reescrita_limpa"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p, den = k / n, 1 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    meia = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centro - meia), min(1.0, centro + meia)


def razao(k: int, n: int) -> str:
    if n == 0:
        return "n/a"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.0f}%; IC95% de Wilson {100 * lo:.0f}% a {100 * hi:.0f}%)"


def matriz_regras(casos: list[Caso], modo: str) -> list[dict]:
    linhas = []
    for regra, r in REGRAS.items():
        vp = fn = fp = vn = extra_disp = extra_quieta = vp_conf = 0
        for c in (c for c in casos if c.modo == modo and regra in c.diag.regras_executadas):
            rot, disparou = rotulo(c, regra), c.disparou(regra)
            if rot == "pos":
                vp += disparou
                fn += not disparou
                vp_conf += disparou and any(a.regra == regra and a.antipadrao and a.confirmado for a in c.diag.achados)
            elif rot == "neg":
                fp += disparou
                vn += not disparou
            else:
                extra_disp += disparou
                extra_quieta += not disparou
        executada = vp + fn + fp + vn + extra_disp + extra_quieta
        linhas.append({
            "modo": modo, "regra": regra, "antipadrao": r.antipadrao, "casos_executada": executada,
            "vp": vp, "vp_confirmado": vp_conf, "fn": fn, "fp": fp, "vn": vn,
            "nao_rotulados_disparou": extra_disp, "nao_rotulados_quieta": extra_quieta,
            "sensibilidade": round(vp / (vp + fn), 3) if vp + fn else "", "especificidade": round(vn / (vn + fp), 3) if vn + fp else "",
            "precisao": round(vp / (vp + fp), 3) if vp + fp else "",
        })
    return linhas


def matriz_aps(casos: list[Caso], modo: str) -> list[dict]:
    linhas = []
    for ap in (*APS, "NENHUM"):
        cs = [c for c in casos if c.modo == modo and c.variante == "original" and c.ap == ap]
        if not cs:
            continue
        linha = {"modo": modo, "declarado": ap, "n": len(cs), **{f"detectou_{d}": sum(d in c.aps_detectados for c in cs) for d in APS}}
        linha["sem_achado_de_ap"] = sum(not c.aps_detectados for c in cs)
        linhas.append(linha)
    return linhas


# ------------------------------------------------------------------------------------------------ saidas
def _csv(caminho: Path, linhas: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    if not linhas:
        caminho.write_text("", encoding="utf-8")
        return
    with caminho.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0]))
        w.writeheader()
        w.writerows(linhas)


def linhas_casos(casos: list[Caso]) -> list[dict]:
    saida = []
    for c in casos:
        extras = sorted({a.regra for a in c.com_ap() if a.antipadrao != c.ap}) if c.grupo == "catalogo" else []
        saida.append({
            "consulta": c.consulta, "grupo": c.grupo, "ap_declarado": c.ap, "papel": c.papel, "variante": c.variante, "modo": c.modo,
            "arquivo_plano": c.arquivo_plano or "", "resultado": resultado(c), "sql_analisavel": c.diag.sql_analisavel,
            "regras_ignoradas": " ".join(c.diag.regras_ignoradas), "regras_disparadas": " ".join(sorted({a.regra for a in c.com_ap()})),
            "aps_detectados": " ".join(sorted(c.aps_detectados)),
            "aps_confirmados": " ".join(sorted(c.diag.antipadroes("confirmado"))),
            "regras_adicionais": " ".join(extras),
            "informativos": " ".join(sorted({a.regra for a in c.diag.achados if not a.antipadrao})),
        })
    return saida


def linhas_achados(casos: list[Caso]) -> list[dict]:
    saida = []
    for c in casos:
        for a in c.diag.achados:
            saida.append({
                "consulta": c.consulta, "variante": c.variante, "modo": c.modo, "id": a.id, "regra": a.regra, "antipadrao": a.antipadrao or "",
                "gravidade": a.gravidade, "nivel": a.nivel, "fontes": "+".join(a.fontes), "no_plano": a.no_plano if a.no_plano is not None else "",
                "operador": a.operador or "", "tabelas": " ".join(a.tabelas), "colunas": " ".join(a.colunas),
                "linha_sql": a.linha_sql or "", "trecho_sql": a.trecho_sql or "", "evidencia": a.evidencia, "notas": " | ".join(a.notas),
            })
    return saida


def _tabela_md(cab: list[str], linhas: list[list]) -> str:
    out = ["| " + " | ".join(cab) + " |", "|" + "|".join("---" for _ in cab) + "|"]
    out += ["| " + " | ".join(str(x) for x in l) + " |" for l in linhas]
    return "\n".join(out)


def relatorio_md(casos: list[Caso], ausentes: list, lim: Limiares, dir_planos: Path, dir_controles: Path, so_sql: bool = False) -> str:
    import sqlglot  # noqa: PLC0415 - so para registrar a versao no relatorio
    modos = [m for m in MODOS if any(c.modo == m for c in casos)]
    n_cat = len({c.consulta for c in casos if c.grupo == "catalogo"})
    n_ctl = len({c.consulta for c in casos if c.grupo == "controle"})
    L = [f"# Validação do instrumento: Diagnóstico (camada 1)\n",
         f"Gerado em {date.today().isoformat()} por `experimento/avaliar_diagnostico.py`. sqlglot {sqlglot.__version__}. "
         f"{n_cat} consultas do catálogo e {n_ctl} controles limpos. Offline (nenhuma consulta ao SQL Server).\n"]
    L.append("## Planos usados\n")
    for art, rot, pasta in (("do", "catálogo", dir_planos), ("dos", "controles", dir_controles)):
        L.append(f"- Pasta de planos {art} {rot}: `{pasta}`; `indice.json` {'presente' if (pasta / 'indice.json').exists() else '**ausente**'}.")
    for m in ("estimado", "real"):
        usados = sum(1 for c in casos if c.modo == m)
        faltam = [a for a in ausentes if a[2] == m]
        L.append(f"- Plano {m}: {usados} casos com plano; {len(faltam)} sem arquivo" + (f" (ex.: {', '.join(f'{i}/{v}' for i, v, _ in faltam[:6])})" if faltam else "") + ".")
    if so_sql:
        L.append("\n> Execução **só de SQL** por opção (`--so-sql`): planos ignorados; AP4 fora do alcance.")
    elif "estimado" not in modos and "real" not in modos:
        L.append("\n> **Avaliação PARCIAL**: nenhum plano disponível. Só as regras de SQL (S1 a S3) foram avaliadas; o AP4 (varredura por falta de índice) "
                 "só é detectável com plano e ficou fora do alcance. Rode `salvar_planos.py` (catálogo e controles) e este script de novo.")
    L.append("\n## Limiares usados (suposições a calibrar)\n")
    L.append(", ".join(f"`{f.name}={getattr(lim, f.name)}`" for f in fields(lim)) + ".")
    L.append("\n## Resumo por modo\n")
    cab = ["Modo", "Originais com AP detectável", "Acertos", "Confirmados", "Só indício", "Omissões", "Reescritas sem resíduo do AP",
           "Reescritas com resíduo no SQL", "Controles sem achado de AP"]
    linhas = []
    for m in modos:
        cm = [c for c in casos if c.modo == m]
        orig = [c for c in cm if c.grupo == "catalogo" and c.variante == "original" and resultado(c) != "fora_do_alcance"]
        conf = sum(resultado(c) == "acerto_confirmado" for c in orig)
        ind = sum(resultado(c) == "acerto_indicio" for c in orig)
        reesc = [c for c in cm if c.grupo == "catalogo" and c.variante == "reescrita"]
        limpas = sum(resultado(c) == "reescrita_limpa" for c in reesc)
        res_sql = sum(resultado(c) == "reescrita_com_residuo_sql" for c in reesc)
        ctl = [c for c in cm if c.grupo == "controle"]
        ok = sum(resultado(c) == "controle_limpo" for c in ctl)
        linhas.append([ROTULO_MODO[m], len(orig), razao(conf + ind, len(orig)), conf, ind, len(orig) - conf - ind,
                       f"{limpas}/{len(reesc)}", f"{res_sql}/{len(reesc)}", razao(ok, len(ctl))])
    L.append(_tabela_md(cab, linhas))
    L.append("\n*Acerto* = o antipadrão declarado aparece em algum achado (confirmado = SQL e plano concordam; indício = só um lado). "
             "Para AP4 (falta de índice) o SQL não é antipadrão: \"confirmado\" significa só que a coluna escaneada ou sugerida para índice é a mesma filtrada no SQL, "
             "um critério mais fraco que o de AP1 (função vista nos dois lados). "
             "*Resíduo* = o antipadrão declarado ainda aparece na reescrita. Intervalos de Wilson com n pequeno são largos: leia como ordem de grandeza.\n")
    for m in modos:
        cm = [c for c in casos if c.modo == m]
        L.append(f"\n## Casos: {ROTULO_MODO[m]}\n")
        linhas = []
        for c in cm:
            extras = sorted({a.regra for a in c.com_ap() if a.antipadrao != c.ap}) if c.grupo == "catalogo" else []
            disp = " ".join(sorted({f"{a.regra}{'*' if a.confirmado else ''}" for a in c.com_ap()})) or "-"
            linhas.append([c.consulta, c.ap + (" (controle)" if c.papel == "controle" else ""), c.variante, resultado(c), disp, " ".join(extras) or "-"])
        L.append(_tabela_md(["Consulta", "AP declarado", "Variante", "Resultado", "Regras que dispararam (* = confirmado)", "Regras de outra classe"], linhas))
        omissoes = [c for c in cm if resultado(c) == "omissao"]
        fps = [c for c in cm if resultado(c) == "falso_positivo"]
        resid = [c for c in cm if resultado(c).startswith("reescrita_com_residuo")]
        for titulo, lista in (("Omissões", omissoes), ("Falsos positivos (controles limpos com achado)", fps), ("Reescritas com resíduo", resid)):
            if lista:
                L.append(f"\n**{titulo} ({ROTULO_MODO[m]}):**")
                for c in lista:
                    for a in (c.com_ap() if titulo != "Omissões" else []):
                        if titulo.startswith("Falsos") or a.antipadrao == c.ap:
                            L.append(f"- {c.consulta} {c.variante}: {a.id} ({a.antipadrao}, {a.nivel}): {a.evidencia[:220]}")
                    if titulo == "Omissões":
                        L.append(f"- {c.consulta} (declarado {c.ap}): nenhuma regra executada o detectou; regras ignoradas: {', '.join(c.diag.regras_ignoradas) or 'nenhuma'}.")
        if m == "sql":
            fora = [c for c in cm if resultado(c) == "fora_do_alcance"]
            if fora:
                L.append(f"\nFora do alcance só com SQL (AP4 exige plano): {', '.join(sorted({c.consulta for c in fora}))}.")
    L.append("\n## Matriz de confusão por regra\n")
    L.append("VP/FN: regra deveria disparar (original com o antipadrão da regra). FP/VN: não deveria (reescrita do mesmo antipadrão e controles limpos). "
             "\"Não rotulados\": consultas de outra classe, não pontuadas. **Leitura dos FN das regras de plano:** o rótulo vem do SQL, mas a regra de plano só vê o "
             "que o plano mostra; na linha de base (sem índices não clusterizados) não há Key Lookup (P2) e o otimizador pode ter trocado a subconsulta por "
             "junção hash (P6). Esses FN dizem que o plano não manifesta o padrão, não que a regra errou; o antipadrão ainda é pego pela regra de SQL.\n")
    for m in modos:
        L.append(f"\n**{ROTULO_MODO[m]}**\n")
        mr = [r for r in matriz_regras(casos, m) if r["casos_executada"]]
        L.append(_tabela_md(["Regra", "AP", "VP (confirm.)", "FN", "FP", "VN", "Não rot. disparou/quieta", "Sensib.", "Especif.", "Precisão"],
                            [[r["regra"], r["antipadrao"], f"{r['vp']} ({r['vp_confirmado']})", r["fn"], r["fp"], r["vn"],
                              f"{r['nao_rotulados_disparou']}/{r['nao_rotulados_quieta']}", *(str(r[k]).replace(".", ",") for k in ("sensibilidade", "especificidade", "precisao"))]
                             for r in mr]))
    L.append("\n## Declarado × detectado (originais)\n")
    for m in modos:
        L.append(f"\n**{ROTULO_MODO[m]}**\n")
        ma = matriz_aps(casos, m)
        L.append(_tabela_md(["Declarado", "n", "detectou AP1", "AP2", "AP3", "AP4", "sem achado de AP"],
                            [[r["declarado"], r["n"], r["detectou_AP1"], r["detectou_AP2"], r["detectou_AP3"], r["detectou_AP4"], r["sem_achado_de_ap"]] for r in ma]))
    L.append("\n## Limites desta avaliação\n")
    L += ["- Os rótulos vêm do catálogo escrito por quem também escreveu o detector: não é avaliação independente. As consultas semeadas são poucas "
          "(uma ou duas por variação) e foram desenhadas para exibir o antipadrão; o desempenho em código real de terceiros **não** foi medido.",
          "- Os planos são da linha de base (sem índices não clusterizados) e de uma única compilação cada, no SQL Server 2022 do experimento; "
          "outras versões, estatísticas ou níveis de compatibilidade podem gerar planos diferentes.",
          "- Limiares são suposições (ver `Limiares`); esta avaliação não os calibrou. Para ver o efeito, repita com `--limiar nome=valor`.",
          "- F5 e S5 são controles de efeito do catálogo (o otimizador pode já tratá-los): o SQL contém o padrão, então contam como positivos do detector.",
          "- **Avaliação na amostra de desenvolvimento**: as consultas do catálogo e dos controles foram vistas enquanto as regras eram escritas e ajustadas "
          "(por exemplo, o filtro PROBE e a contagem de execuções do lado interno vieram de planos desta própria coleta). Acerto de 100% nas regras de SQL "
          "é, portanto, o esperado e **não mede generalização**; serve como teste de regressão e de coerência entre catálogo e detector.",
          "- P4 (PlanAffectingConvert) não tem positivo rotulável no catálogo (nenhuma consulta com tipos incompatíveis); P2 e P6 só aparecem em planos "
          "de variantes com índice (fora desta avaliação, que usa a linha de base); ver `knowledge/24-diagnostico.md`."]
    return "\n".join(L) + "\n"


def _parse_limiares(itens: list[str]) -> Limiares:
    tipos = {f.name: f.type for f in fields(Limiares)}
    mud = {}
    for it in itens:
        nome, _, val = it.partition("=")
        if nome not in tipos:
            raise SystemExit(f"limiar desconhecido: {nome} (validos: {', '.join(tipos)})")
        mud[nome] = int(val.replace("_", "")) if "int" in str(tipos[nome]) else float(val)
    return replace(Limiares(), **mud)


# ------------------------------------------------------------------------------------------------ conjunto de VALIDACAO (heldout)
# Formato: como o catalogo.toml, mais as listas `esperado`, `tolerado` e `proibido` de classes AP1..AP4 por consulta.
# Pontuacao (PRE-REGISTRO.md, secao 5):
#   acerto   = todas as classes de `esperado` apontadas;  omissao = alguma de `esperado` nao apontada;
#   falso positivo = classe apontada fora de esperado + tolerado (violar `proibido` e o caso grave);
#   esperado vazio = consulta limpa (qualquer classe fora de `tolerado` e falso positivo).
# "Apontada" = algum achado com aquele antipadrao, em qualquer nivel (confirmado ou indicio). Achado sem antipadrao nao conta.
# Alem da pontuacao estrita, o relatorio traz uma leitura "dentro do alcance" (em modo so SQL, o AP4 e inatingivel por construcao):
# ela NAO e a regra de pontuacao do pre-registro e vem sempre depois da estrita.
@dataclass
class ConsultaVal:
    id: str
    titulo: str
    sql: str
    esperado: frozenset[str]
    tolerado: frozenset[str]
    proibido: frozenset[str]
    antipadrao: str


@dataclass
class CasoVal:
    consulta: ConsultaVal
    modo: str
    arquivo_plano: str | None
    diag: Diagnostico
    pontos: dict

    @property
    def aps(self) -> set[str]:
        return {a.antipadrao for a in self.diag.achados if a.antipadrao}


def _classes(valor, campo: str, qid: str) -> frozenset[str]:
    s = frozenset(valor)
    if not s <= set(APS):
        raise ValueError(f"{qid}: {campo} tem classe fora de AP1..AP4: {sorted(s - set(APS))}")
    return s


def carregar_validacao(caminho: Path) -> list[ConsultaVal]:
    dados = tomllib.loads(Path(caminho).read_text(encoding="utf-8"))
    saida = []
    for c in dados["consulta"]:
        qid = c["id"]
        esperado, tolerado, proibido = (_classes(c[k], k, qid) for k in ("esperado", "tolerado", "proibido"))
        if proibido & (esperado | tolerado):
            raise ValueError(f"{qid}: rotulo inconsistente, `proibido` repete classe de esperado/tolerado: {sorted(proibido & (esperado | tolerado))}")
        saida.append(ConsultaVal(qid, c.get("titulo", ""), c["sql_original"].strip(), esperado, tolerado, proibido, c.get("antipadrao", "")))
    ids = [c.id for c in saida]
    if len(ids) != len(set(ids)):
        raise ValueError("ids duplicados no conjunto de validacao")
    return saida


def tem_rotulos_de_validacao(caminho: Path) -> bool:
    return any("esperado" in c for c in tomllib.loads(Path(caminho).read_text(encoding="utf-8")).get("consulta", []))


def pontuar(c: ConsultaVal, apontadas: set[str], alcance: set[str] | None = None) -> dict:
    """Pontuacao estrita (alcance=None) ou restrita as classes ao alcance do modo (omissao so conta classe alcancavel)."""
    exigido = c.esperado if alcance is None else c.esperado & alcance
    omissoes = sorted(exigido - apontadas)
    fps = sorted(apontadas - c.esperado - c.tolerado)
    proibidos = sorted(apontadas & c.proibido)
    toleradas = sorted((apontadas & c.tolerado) - c.esperado)
    if not c.esperado:
        resultado_ = "limpa_correta" if not fps else "limpa_com_falso_positivo"
    elif not omissoes:
        resultado_ = "acerto" if not fps else "acerto_com_falso_positivo"
    else:
        resultado_ = "omissao" if not fps else "omissao_e_falso_positivo"
    return {"resultado": resultado_, "omissoes": omissoes, "falsos_positivos": fps, "proibidos_violados": proibidos, "toleradas": toleradas}


def executar_validacao(consultas: list[ConsultaVal], dir_planos: Path, so_sql: bool, lim: Limiares):
    casos: list[CasoVal] = []
    ausentes: list[tuple[str, str]] = []
    for c in consultas:
        for modo in MODOS:
            xml, nome = None, None
            if modo != "sql":
                if so_sql:
                    continue
                caminho = dir_planos / c.id / f"original.{modo}.sqlplan"
                if not caminho.exists():
                    ausentes.append((c.id, modo))
                    continue
                xml, nome = caminho.read_text(encoding="utf-8"), str(caminho.relative_to(dir_planos.parent))
            d = diagnosticar(c.sql, xml, lim)
            apontadas = {a.antipadrao for a in d.achados if a.antipadrao}
            pontos = pontuar(c, apontadas)
            alc = pontuar(c, apontadas, alcance=SEM_PLANO if modo == "sql" else set(APS))
            pontos.update(resultado_no_alcance=alc["resultado"], omissoes_no_alcance=alc["omissoes"])
            casos.append(CasoVal(c, modo, nome, d, pontos))
    return casos, ausentes


def matriz_regras_validacao(casos: list[CasoVal], modo: str) -> list[dict]:
    """Por regra: VP/FN (a classe da regra e `esperada`), FP/VN (a classe nao e esperada nem tolerada), tolerada (nao pontuada)."""
    linhas = []
    for regra, r in REGRAS.items():
        vp = fn = fp = vn = tol = proib = 0
        for k in (k for k in casos if k.modo == modo and regra in k.diag.regras_executadas):
            c, disparou = k.consulta, any(a.regra == regra and a.antipadrao for a in k.diag.achados)
            if r.antipadrao in c.esperado and regra != "P4":   # P4 (conversao de tipo): sem positivo rotulavel, como na avaliacao de desenvolvimento
                vp += disparou
                fn += not disparou
            elif r.antipadrao in c.tolerado:
                tol += disparou
            elif r.antipadrao not in c.esperado:
                fp += disparou
                vn += not disparou
                proib += disparou and r.antipadrao in c.proibido
        linhas.append({
            "modo": modo, "regra": regra, "antipadrao": r.antipadrao, "vp": vp, "fn": fn, "fp": fp, "fp_proibido": proib, "vn": vn,
            "toleradas_disparou": tol,
            "sensibilidade": round(vp / (vp + fn), 3) if vp + fn else "", "precisao": round(vp / (vp + fp), 3) if vp + fp else "",
        })
    return linhas


def sha256_arquivo(caminho: Path) -> str:
    return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()


def sha_do_pre_registro(rel: str) -> str | None:
    """SHA-256 registrado no PRE-REGISTRO.md para o arquivo `rel` (linha de tabela `| `rel` | `hash` |`)."""
    pr = AQUI / "PRE-REGISTRO.md"
    m = re.search(rf"`{re.escape(rel)}`\s*\|\s*`([0-9a-f]{{64}})`", pr.read_text(encoding="utf-8")) if pr.exists() else None
    return m.group(1) if m else None


def _lista(x) -> str:
    return " ".join(sorted(x)) or "-"


def linhas_casos_validacao(casos: list[CasoVal]) -> list[dict]:
    return [{
        "consulta": k.consulta.id, "titulo": k.consulta.titulo, "modo": k.modo, "arquivo_plano": k.arquivo_plano or "",
        "esperado": _lista(k.consulta.esperado), "tolerado": _lista(k.consulta.tolerado), "proibido": _lista(k.consulta.proibido),
        "apontadas": _lista(k.aps), "apontadas_confirmadas": _lista(k.diag.antipadroes("confirmado")),
        "resultado": k.pontos["resultado"], "omissoes": _lista(k.pontos["omissoes"]), "falsos_positivos": _lista(k.pontos["falsos_positivos"]),
        "proibidos_violados": _lista(k.pontos["proibidos_violados"]), "toleradas": _lista(k.pontos["toleradas"]),
        "resultado_no_alcance": k.pontos["resultado_no_alcance"], "omissoes_no_alcance": _lista(k.pontos["omissoes_no_alcance"]),
        "regras_disparadas": _lista({a.regra for a in k.diag.achados if a.antipadrao}), "informativos": _lista({a.regra for a in k.diag.achados if not a.antipadrao}),
        "sql_analisavel": k.diag.sql_analisavel, "regras_ignoradas": " ".join(k.diag.regras_ignoradas),
    } for k in casos]


def linhas_achados_validacao(casos: list[CasoVal]) -> list[dict]:
    return [{
        "consulta": k.consulta.id, "modo": k.modo, "id": a.id, "regra": a.regra, "antipadrao": a.antipadrao or "", "gravidade": a.gravidade,
        "nivel": a.nivel, "fontes": "+".join(a.fontes), "no_plano": a.no_plano if a.no_plano is not None else "", "linha_sql": a.linha_sql or "",
        "trecho_sql": a.trecho_sql or "", "evidencia": a.evidencia, "notas": " | ".join(a.notas),
    } for k in casos for a in k.diag.achados]


def resumo_validacao(casos: list[CasoVal], modo: str) -> dict:
    cm = [k for k in casos if k.modo == modo]
    com = [k for k in cm if k.consulta.esperado]
    limpas = [k for k in cm if not k.consulta.esperado]
    return {
        "modo": modo, "consultas": len(cm), "com_esperado": len(com), "limpas": len(limpas),
        "acertos": sum(not k.pontos["omissoes"] for k in com), "omissoes": sum(bool(k.pontos["omissoes"]) for k in com),
        "acertos_no_alcance": sum(not k.pontos["omissoes_no_alcance"] for k in com),
        "limpas_corretas": sum(not k.pontos["falsos_positivos"] for k in limpas),
        "consultas_com_falso_positivo": sum(bool(k.pontos["falsos_positivos"]) for k in cm),
        "consultas_com_proibido": sum(bool(k.pontos["proibidos_violados"]) for k in cm),
        "total_falsos_positivos": sum(len(k.pontos["falsos_positivos"]) for k in cm),
        "total_proibidos": sum(len(k.pontos["proibidos_violados"]) for k in cm),
    }


def relatorio_validacao_md(casos: list[CasoVal], ausentes: list, lim: Limiares, arquivo: Path, dir_planos: Path, so_sql: bool, execucao: str) -> str:
    import sqlglot  # noqa: PLC0415
    from tcclab import diagnostico as mod_diag  # noqa: PLC0415 - so para o caminho e o hash do detector
    modos = [m for m in MODOS if any(k.modo == m for k in casos)]
    consultas = {k.consulta.id: k.consulta for k in casos}
    sha_c, sha_d = sha256_arquivo(arquivo), sha256_arquivo(Path(mod_diag.__file__))
    pre_c = sha_do_pre_registro("experimento/heldout.toml") if arquivo.name == "heldout.toml" else None
    pre_d = sha_do_pre_registro("nucleo/src/tcclab/diagnostico.py")
    coincide = lambda a, b: "não aplicável (arquivo fora do pré-registro)" if b is None else ("sim" if a == b else "**NÃO: difere do congelado**")  # noqa: E731
    padrao = lim == Limiares()
    L = ["# Validação do Diagnóstico em conjunto separado (heldout)\n",
         f"Execução `{execucao}`, gerada em {date.today().isoformat()} por `experimento/avaliar_diagnostico.py`. sqlglot {sqlglot.__version__}. "
         f"{len(consultas)} consultas. Offline (nenhuma consulta ao SQL Server).\n",
         "## Integridade (congelamento do pré-registro)\n",
         f"- Conjunto `{arquivo.name}`: SHA-256 `{sha_c}`; coincide com o pré-registro: {coincide(sha_c, pre_c)}.",
         f"- Detector `diagnostico.py`: SHA-256 `{sha_d}`; coincide com o pré-registro: {coincide(sha_d, pre_d)}.",
         f"- Limiares: {'valores padrão do detector congelado' if padrao else '**NÃO são os padrão** (' + ', '.join(f'{f.name}={getattr(lim, f.name)}' for f in fields(lim) if getattr(lim, f.name) != getattr(Limiares(), f.name)) + ')'}."]
    if not (sha_c == pre_c or pre_c is None) or not (sha_d == pre_d or pre_d is None) or not padrao:
        L.append("- **Esta execução deve ser rotulada \"após ajuste\"** (PRE-REGISTRO.md, seção 5, regra iii): algo mudou desde o congelamento.")
    L.append("\n## Estado dos modos\n")
    for m in MODOS:
        if m in modos:
            L.append(f"- {ROTULO_MODO[m]}: executado ({sum(k.modo == m for k in casos)} casos).")
        elif m == "sql":
            L.append("- Só SQL: não executado.")
        elif so_sql:
            L.append(f"- {ROTULO_MODO[m]}: **PENDENTE** (execução só de SQL por opção; os planos deste conjunto só podem ser gerados depois do fim da R1, pois a geração usa o banco).")
        else:
            falt = [i for i, mm in ausentes if mm == m]
            L.append(f"- {ROTULO_MODO[m]}: **PENDENTE** ({len(falt)} de {len(consultas)} planos ausentes em `{dir_planos}`).")
    L.append("\n## Resumo por modo\n")
    cab = ["Modo", "Com esperado: acertos (estrito)", "Omissões", "Limpas sem falso positivo", "Consultas com falso positivo", "Falsos positivos (classes)",
           "Proibidos violados"]
    linhas = []
    for m in modos:
        r = resumo_validacao(casos, m)
        linhas.append([ROTULO_MODO[m], razao(r["acertos"], r["com_esperado"]), r["omissoes"], razao(r["limpas_corretas"], r["limpas"]),
                       razao(r["consultas_com_falso_positivo"], r["consultas"]), r["total_falsos_positivos"], f"{r['total_proibidos']} em {r['consultas_com_proibido']} consulta(s)"])
    L.append(_tabela_md(cab, linhas))
    L.append("\n**Leitura dentro do alcance do modo** (NÃO é a regra de pontuação do pré-registro; só separa a omissão por limite do modo da omissão por defeito do detector):\n")
    linhas = []
    for m in modos:
        r = resumo_validacao(casos, m)
        linhas.append([ROTULO_MODO[m], razao(r["acertos_no_alcance"], r["com_esperado"]),
                       "AP1 a AP3 (AP4 exige plano)" if m == "sql" else "AP1 a AP4"])
    L.append(_tabela_md(["Modo", "Acertos considerando só as classes ao alcance", "Alcance"], linhas))
    for m in modos:
        L.append(f"\n## Por consulta: {ROTULO_MODO[m]}\n")
        linhas = []
        for k in (k for k in casos if k.modo == m):
            c, p = k.consulta, k.pontos
            linhas.append([c.id, c.titulo[:60], _lista(c.esperado), _lista(c.tolerado), _lista(k.aps), p["resultado"], _lista(p["omissoes"]),
                           _lista(p["falsos_positivos"]), _lista(p["proibidos_violados"]), p["resultado_no_alcance"]])
        L.append(_tabela_md(["Consulta", "Título", "Esperado", "Tolerado", "Apontadas", "Resultado (estrito)", "Omissões", "Falsos positivos",
                             "Proibidos violados", "Resultado no alcance"], linhas))
        det = [k for k in casos if k.modo == m and (k.pontos["omissoes"] or k.pontos["falsos_positivos"])]
        if det:
            L.append(f"\n**Omissões e falsos positivos ({ROTULO_MODO[m]}), como saíram:**")
            for k in det:
                c, p = k.consulta, k.pontos
                for ap in p["omissoes"]:
                    nota = " (inatingível sem plano: nenhuma regra de SQL mapeia para AP4)" if m == "sql" and ap not in SEM_PLANO else ""
                    L.append(f"- {c.id}: **omissão** de {ap}{nota}. Apontadas: {_lista(k.aps)}.")
                for ap in p["falsos_positivos"]:
                    grave = " **[PROIBIDO]**" if ap in p["proibidos_violados"] else ""
                    for a in (a for a in k.diag.achados if a.antipadrao == ap):
                        L.append(f"- {c.id}: **falso positivo** {ap}{grave} pela regra {a.regra} ({a.nivel}): {a.evidencia[:240]}")
        nao_anal = [k for k in casos if k.modo == m and not k.diag.sql_analisavel]
        if nao_anal:
            L.append(f"\n**SQL não analisável ({ROTULO_MODO[m]}):** {', '.join(k.consulta.id for k in nao_anal)}.")
    L.append("\n## Por regra\n")
    L.append("VP/FN: a classe da regra é `esperada` na consulta. FP/VN: a classe não é esperada nem tolerada (FP proibido = violou `proibido`). "
             "\"Toleradas\": a regra disparou numa classe tolerada (não pontuada). Regras de plano só aparecem nos modos com plano.\n")
    for m in modos:
        mr = [r for r in matriz_regras_validacao(casos, m) if (m != "sql" or r["regra"][0] == "S")]
        L.append(f"\n**{ROTULO_MODO[m]}**\n")
        L.append(_tabela_md(["Regra", "AP", "VP", "FN", "FP (proibido)", "VN", "Toleradas", "Sensib.", "Precisão"],
                            [[r["regra"], r["antipadrao"], r["vp"], r["fn"], f"{r['fp']} ({r['fp_proibido']})", r["vn"], r["toleradas_disparou"],
                              *(str(r[k]).replace(".", ",") for k in ("sensibilidade", "precisao"))] for r in mr]))
    L.append("\n## Ressalvas\n")
    L += ["- **n pequeno e exploratório.** São 13 consultas (10 com classe esperada e 3 limpas); os intervalos de Wilson (95%) acima são largos e nenhum resultado "
          "aqui estabelece taxa de acerto ou de falso positivo em código real. Mesmo 10/10 deixaria o limite inferior do IC95% de Wilson em ~72%.",
          "- **Rótulos feitos pela coordenação do projeto (assistente), antes da execução**, mas não por pessoa independente do detector; o autor do TCC ainda não os revisou.",
          "- **Pontuação estrita em modo só SQL:** consultas com AP4 em `esperado` (H6, H7, H8, H9, H10) contam como omissão porque nenhuma regra de SQL pode apontar AP4 (a "
          "classe vem do plano). Isso é limite do modo, não necessariamente defeito do detector; por isso a leitura \"dentro do alcance\" vem ao lado, sem substituir a estrita.",
          "- **Execução com plano (estimado e real): pendente** até depois do fim da R1. Rodar `avaliar_diagnostico.py --catalogo heldout.toml` (sem `--so-sql`) depois que "
          "`salvar_planos.py --catalogo heldout.toml --saida planos_heldout` gerar os planos; essa segunda execução usa uma `--execucao` com outro nome.",
          "- Se o detector ou os limiares forem alterados depois desta execução, o conjunto passa a ser de desenvolvimento (PRE-REGISTRO.md, seção 5, regra iii)."]
    return "\n".join(L) + "\n"


def main_validacao(a) -> int:
    arquivo = Path(a.catalogo)
    dir_planos = Path(a.planos) if a.planos else AQUI / "planos_heldout"
    execucao = a.execucao or ("so_sql" if a.so_sql else "com_planos")
    saida = Path(a.saida) if a.saida else AQUI / "resultados" / "diagnostico_heldout"
    prefixo = f"heldout_{execucao}_"
    if (saida / f"{prefixo}diagnostico.md").exists() and not a.sobrescrever:
        print(f"RECUSADO: {saida / (prefixo + 'diagnostico.md')} ja existe. O conjunto de validacao roda UMA vez por modo (PRE-REGISTRO.md, secao 5); "
              "use --execucao com outro nome (ex.: apos_ajuste) ou --sobrescrever conscientemente.", file=sys.stderr)
        return 2
    lim = _parse_limiares(a.limiar)
    consultas = carregar_validacao(arquivo)
    if not a.so_sql and not (dir_planos / "indice.json").exists():
        print(f"AVISO: {dir_planos / 'indice.json'} nao existe; os modos com plano ficam pendentes.", file=sys.stderr)
    casos, ausentes = executar_validacao(consultas, dir_planos, a.so_sql, lim)
    _csv(saida / f"{prefixo}casos.csv", linhas_casos_validacao(casos))
    _csv(saida / f"{prefixo}achados.csv", linhas_achados_validacao(casos))
    _csv(saida / f"{prefixo}regras.csv", [l for m in MODOS if any(k.modo == m for k in casos) for l in matriz_regras_validacao(casos, m)])
    _csv(saida / f"{prefixo}resumo.csv", [resumo_validacao(casos, m) for m in MODOS if any(k.modo == m for k in casos)])
    md = relatorio_validacao_md(casos, ausentes, lim, arquivo, dir_planos, a.so_sql, execucao)
    (saida / f"{prefixo}diagnostico.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"Arquivos em {saida} (prefixo {prefixo})", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--catalogo", default=str(AQUI / "catalogo.toml"))
    p.add_argument("--controles", default=str(AQUI / "controles_limpos.toml"))
    p.add_argument("--planos", default=None, help="planos do catalogo: <ID>/{original,reescrita}.{estimado,real}.sqlplan "
                   "(padrao: experimento/planos; no conjunto de validacao: experimento/planos_heldout)")
    p.add_argument("--planos-controles", default=str(AQUI / "planos_controles"), help="planos dos controles limpos (mesmo formato)")
    p.add_argument("--saida", default=None, help="padrao: resultados/diagnostico (ou resultados/diagnostico_heldout na validacao)")
    p.add_argument("--execucao", default=None, help="so na validacao: rotulo da execucao (padrao so_sql ou com_planos); vira prefixo dos arquivos")
    p.add_argument("--sobrescrever", action="store_true", help="so na validacao: permite regravar uma execucao ja existente (evite)")
    p.add_argument("--so-sql", action="store_true", help="ignora planos (so as regras de SQL)")
    p.add_argument("--limiar", action="append", default=[], metavar="NOME=VALOR", help="sobrescreve um limiar (repetivel)")
    a = p.parse_args(argv)
    if tem_rotulos_de_validacao(Path(a.catalogo)):   # arquivo com esperado/tolerado/proibido = conjunto de validacao (heldout)
        return main_validacao(a)
    dir_planos, dir_controles = Path(a.planos or AQUI / "planos"), Path(a.planos_controles)
    if not a.so_sql:
        for pasta in (dir_planos, dir_controles):
            if not (pasta / "indice.json").exists():
                print(f"AVISO: {pasta / 'indice.json'} nao existe (lote de planos incompleto ou nao gerado); avaliacao com plano parcial.", file=sys.stderr)
    lim = _parse_limiares(a.limiar)
    casos, ausentes = gerar_casos(Path(a.catalogo), Path(a.controles), dir_planos, dir_controles, a.so_sql, lim)
    saida = Path(a.saida or AQUI / "resultados" / "diagnostico")
    _csv(saida / "avaliacao_casos.csv", linhas_casos(casos))
    _csv(saida / "avaliacao_achados.csv", linhas_achados(casos))
    _csv(saida / "avaliacao_matriz_regras.csv", [l for m in MODOS if any(c.modo == m for c in casos) for l in matriz_regras(casos, m)])
    _csv(saida / "avaliacao_matriz_antipadroes.csv", [l for m in MODOS if any(c.modo == m for c in casos) for l in matriz_aps(casos, m)])
    md = relatorio_md(casos, ausentes, lim, dir_planos, dir_controles, a.so_sql)
    (saida / "avaliacao_diagnostico.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"Arquivos em {saida}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
