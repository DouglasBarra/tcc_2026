"""Relatorio para o artigo: CSV em formato longo, tabelas LaTeX (booktabs), figuras PDF e "numeros do artigo".

Tudo sai de `analise.Analise` (que le so o SQLite); nenhum numero e digitado a mao. Convencoes de formato:
  - CSV/JSON: numeros "de maquina" (ponto decimal, sem separador de milhar, precisao total);
  - tabelas .tex e .md: pt-BR (virgula decimal, ponto como separador de milhar), pensadas para \\input;
  - figuras: PDF vetorial (fontes TrueType embutidas), paleta Okabe-Ito, rotulos em portugues.
matplotlib so e importado dentro das funcoes de figura (o restante exige apenas numpy e scipy).
O pacote LaTeX `booktabs` deve estar carregado no documento que fizer o \\input.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from .analise import SEM_ANTIPADRAO, Analise, json_seguro, para_json
from .stats import resumir

# ----------------------------------------------------------------------------------------- formatacao pt-BR
ROTULO_METRICA = {"logical_reads": "Leituras lógicas", "physical_reads": "Leituras físicas",
                  "read_ahead_reads": "Read-ahead", "cpu_ms": "CPU (ms)", "elapsed_ms": "Decorrido (ms)",
                  "cpu_us": "CPU (µs)", "elapsed_us": "Decorrido (µs)"}
ROTULO_METRICA_TEX = ROTULO_METRICA | {"cpu_us": "CPU ($\\mu$s)", "elapsed_us": "Decorrido ($\\mu$s)"}
ND = "--"
NOME_GRUPO = {"prescricao_do_framework": "prescrição", "nativo_vs_prescricao": "nativo vs prescrição"}
ROTULO_ALTERNATIVA = {"two-sided": "bilateral", "less": "unilateral, redução", "greater": "unilateral, aumento"}

# Okabe-Ito (acessivel a daltonismo): azul para a base, demais para os tratamentos
PALETA_BASE = "#0072B2"
PALETA_TRAT = ["#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#000000"]
MARCADORES = ["o", "s", "^", "D", "v", "P"]


def _ok(x) -> bool:
    return x is not None and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def _agrupar(txt: str) -> str:
    """'1,234,567.89' (formato en) -> '1.234.567,89' (pt-BR), sem depender de locale."""
    return txt.replace(",", "\0").replace(".", ",").replace("\0", ".")


def fmt_num(x, casas: int = 0, milhar: bool = True) -> str:
    if not _ok(x):
        return ND
    return _agrupar(f"{x:{',' if milhar else ''}.{casas}f}")


def fmt_sig(x, sig: int = 3) -> str:
    """`sig` algarismos significativos, virgula decimal (razoes: 0,500; 0,000523; 12,5)."""
    if not _ok(x):
        return ND
    if x == 0:
        return fmt_num(0.0, sig - 1)
    casas = max(0, sig - 1 - math.floor(math.log10(abs(x))))
    return fmt_num(x, casas)


def fmt_auto(x) -> str:
    """Medianas: inteiro sem casas; 100 ou mais com 1 casa; abaixo disso 3 algarismos significativos."""
    if not _ok(x):
        return ND
    if abs(x - round(x)) < 1e-9:
        return fmt_num(round(x), 0)
    return fmt_num(x, 1) if abs(x) >= 100 else fmt_sig(x, 3)


def fmt_pct(x, casas: int = 1) -> str:
    return ND if not _ok(x) else fmt_num(x, casas) + "%"


def fmt_p(p) -> str:
    if not _ok(p):
        return ND
    return "<0,001" if p < 0.001 else fmt_num(p, 3)


def fmt_ic(lo, hi, f=fmt_sig) -> str:
    return ND if not (_ok(lo) and _ok(hi)) else f"[{f(lo)}; {f(hi)}]"


def esc(txt) -> str:
    """Escapa texto para LaTeX (nomes de consulta, antipadroes...)."""
    s = str(txt)
    for a, b in (("\\", "\\textbackslash{}"), ("&", "\\&"), ("%", "\\%"), ("_", "\\_"), ("#", "\\#"), ("$", "\\$"),
                 ("{", "\\{"), ("}", "\\}"), ("~", "\\textasciitilde{}"), ("^", "\\textasciicircum{}")):
        s = s.replace(a, b)
    return s


def tx(numero: str) -> str:
    """Numero formatado -> LaTeX: escapa % e usa sinal de menos matematico e '<' em modo matematico."""
    if numero == ND:
        return numero
    s = numero.replace("%", "\\%")
    if s.startswith("<"):
        s = "$<$" + s[1:]
    if s.startswith("-") and len(s) > 1:
        s = "$-$" + s[1:]
    return s.replace("[-", "[$-$").replace("; -", "; $-$")


# ----------------------------------------------------------------------------------------- CSV
def _escrever_csv(caminho: Path, linhas: list[dict]) -> bool:
    if not linhas:
        return False
    cols = list(linhas[0])
    with caminho.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for l in linhas:
            if list(l) != cols:
                raise ValueError(f"linhas de {caminho.name} com colunas diferentes")
            w.writerow({k: ("" if v is None else v) for k, v in l.items()})
    return True


def escrever_csv(analise: Analise, pasta: str | Path) -> list[Path]:
    """summary.csv, comparisons.csv, aggregate.csv (+ verdicts, determinism, outliers, consultas); formato longo."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    mapa = {"summary.csv": analise.resumos, "comparisons.csv": analise.comparacoes, "aggregate.csv": analise.agregados,
            "verdicts.csv": analise.veredictos, "determinism.csv": analise.determinismo, "escrita.csv": analise.escrita,
            "descartados.csv": analise.descartados,
            "outliers.csv": analise.atipicos, "queries.csv": analise.consultas}
    return [pasta / n for n, l in mapa.items() if _escrever_csv(pasta / n, l)]


def escrever_json(analise: Analise, pasta: str | Path) -> list[Path]:
    """analise.json (tudo) e criterios.json (os criterios usados, com o hash)."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "analise.json").write_text(para_json(analise), encoding="utf-8")
    (pasta / "criterios.json").write_text(
        json.dumps({"criterios": analise.criterios.como_dict(), "criterios_sha": analise.criterios.sha(),
                    "experimentos": analise.meta["experimentos"], "banco": analise.meta["banco"],
                    "banco_sha256": analise.meta["banco_sha256"], "numpy": analise.meta["numpy"],
                    "scipy": analise.meta["scipy"], "tukey": "1,5 x IQR (fixo em stats.py)",
                    "metodo_ic": "bootstrap percentil, semente fixa por comparacao = [semente, crc32(chave)]"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return [pasta / "analise.json", pasta / "criterios.json"]


# ----------------------------------------------------------------------------------------- tabelas LaTeX
def _tabular(alinh: str, cabecalho: list[str], linhas: list[list[str] | None], notas: list[str], cab_comentario: str,
             tamanho: str = "\\small", grupos: list[tuple[str, int, int]] | None = None) -> str:
    """tabular booktabs dentro de um grupo ({\\small ...}) para caber na pagina. `grupos`: cabecalho de 1o nivel
    [(texto, coluna_inicial_1based, largura)] com \\cmidrule; `cabecalho` e o de 2o nivel (ou unico)."""
    n = len(alinh)
    out = [f"% {cab_comentario}", "% requer \\usepackage{booktabs}; arquivo gerado por tcclab.relatorio (nao editar a mao)",
           f"{{{tamanho}\\setlength{{\\tabcolsep}}{{4pt}}", f"\\begin{{tabular}}{{{alinh}}}", "\\toprule"]
    if grupos:
        celulas, col = [], 1
        for texto, ini_, larg in grupos:
            celulas += [""] * (ini_ - col) + [f"\\multicolumn{{{larg}}}{{c}}{{{texto}}}"]
            col = ini_ + larg
        celulas += [""] * (n - col + 1)
        out.append(" & ".join(celulas) + " \\\\")
        out.append("".join(f"\\cmidrule(lr){{{i}-{i + w - 1}}}" for _, i, w in grupos))
    out += [" & ".join(cabecalho) + " \\\\", "\\midrule"]
    for l in linhas:
        out.append("\\midrule" if l is None else " & ".join(l) + " \\\\")
    out += ["\\bottomrule", "\\end{tabular}\\par"]
    if notas:   # notas abaixo da tabela, na largura do texto (dentro do tabular esticariam as tabelas estreitas)
        out += ["\\vspace{3pt}", "\\begin{minipage}{\\linewidth}\\footnotesize", "\\par\n".join(notas), "\\end{minipage}"]
    out += ["}", ""]
    return "\n".join(out)


def _ic_rot(analise: Analise) -> str:
    return f"IC{round(analise.criterios.nivel_ic * 100):d}\\%"


def _indice_comparacoes(analise: Analise) -> dict:
    return {(c["experiment_id"], c["query_id"], c["tratamento_variant_id"], c["regime"], c["metrica"]): c
            for c in analise.comparacoes if c["comparacao"] != "nativo_vs_prescricao"}


def regimes_da_analise(analise: Analise) -> list[str]:
    return sorted({v["regime"] for v in analise.veredictos})


def _principal(analise: Analise, papel: str) -> bool:
    return papel in analise.criterios.papeis_principais


def _nome(analise: Analise, i: dict) -> str:
    """Rotulo da consulta; controles levam o papel no nome para nao se confundirem com as principais."""
    return i["consulta"] if _principal(analise, i.get("papel", "principal")) else f"{i['consulta']} ({i['papel']})"


def _veredictos(analise: Analise, regime: str, controles: bool = False) -> list[dict]:
    return sorted((v for v in analise.veredictos if v["regime"] == regime and v["comparacao"] == "principal"
                   and _principal(analise, v["papel"]) != controles),
                  key=lambda v: (v["antipadrao"], v["consulta"], v["tratamento_kind"]))


def _flags(itens: list[dict], antipadrao: bool = True) -> tuple[bool, bool]:
    """(mostrar antipadrao, mostrar tratamento): colunas so aparecem se houver mais de um valor."""
    return (antipadrao and len({i["antipadrao"] for i in itens}) > 1, len({i["tratamento_kind"] for i in itens}) > 1)


def _prefixo(i: dict, flags: tuple[bool, bool]) -> list[str]:
    return [esc(i["consulta"])] + ([esc(i["antipadrao"])] if flags[0] else []) + ([esc(i["tratamento_kind"])] if flags[1] else [])


def _cab_prefixo(flags: tuple[bool, bool]) -> list[str]:
    return ["Consulta"] + (["Antipadrão"] if flags[0] else []) + (["Tratamento"] if flags[1] else [])


def _cota(c: dict, sufixo: str = "") -> bool:
    return c["regra_zero" + sufixo] not in ("", "ambas_zero")


def tabela_resumo_consulta(analise: Analise, regime: str, controles: bool = False) -> str:
    """Leituras logicas por consulta: mediana antes/depois, razao com IC, reducao e criterio de melhoria.
    `controles=True`: so as consultas de papel fora da analise principal (tabela propria dos controles)."""
    crit = analise.criterios
    idx = _indice_comparacoes(analise)
    vs = _veredictos(analise, regime, controles)
    flags = _flags(vs)
    linhas, tem_zero, sem_escrita, degenerado = [], False, False, False
    for v in vs:
        c = idx[(v["experiment_id"], v["query_id"], v["tratamento_variant_id"], v["regime"], v["metrica_criterio"])]
        marca = "$^{*}$" if _cota(c) else ""
        tem_zero |= bool(marca)
        sem_escrita |= v["criterio_escrita"] == "sem_dados"
        degenerado |= c["ic_lo"] == c["ic_hi"]
        linhas.append(_prefixo(v, flags) + [tx(fmt_auto(v["mediana_antes"])), tx(fmt_auto(v["mediana_depois"])),
                                            tx(fmt_sig(c["razao_reg"])) + marca, tx(fmt_ic(c["ic_lo"], c["ic_hi"])),
                                            tx(fmt_pct(v["reducao_pct"])), "sim" if v["melhoria_significativa"] else "não"])
    cab = _cab_prefixo(flags) + ["Antes", "Depois", "Razão", _ic_rot(analise), "Redução", "Critério"]
    notas = [f"Regime {esc(regime)}. Mediana de {esc(ROTULO_METRICA[crit.metrica_criterio].lower())} (antes e depois); razão = "
             f"mediana depois/antes. Critério de melhoria: redução $\\geq$ {tx(fmt_num(crit.reducao_minima_pct, 0))}\\% e "
             f"{_ic_rot(analise)} da razão abaixo de 1" +
             ("; custo de escrita não avaliado (sem dados)." if sem_escrita else
              "; custo de escrita reportado à parte, sem limiar." if crit.degradacao_escrita_max_pct is None else
              f", com custo de escrita não degradado além de {tx(fmt_num(crit.degradacao_escrita_max_pct, 0))}\\%.") +
             (" Consultas de controle: fora da análise principal." if controles else "")]
    if tem_zero:
        notas.append(f"$^{{*}}$ Mediana igual a zero: razão calculada com piso de {tx(fmt_num(crit.piso_zero, 1))} (cota, não valor exato).")
    if degenerado:
        notas.append("IC degenerado (limites iguais) onde a métrica é constante nas repetições (desvio padrão zero): a incerteza "
                     "das repetições é nula e o intervalo não mede variação entre consultas.")
    return _tabular("l" * len(_cab_prefixo(flags)) + "rrrrrc", cab, linhas, notas,
                    f"{'tab_controles' if controles else 'tab_resumo_consulta'}; regime {regime}; "
                    f"experimentos {analise.meta['experimentos']}; criterios_sha {crit.sha()}")


def tabela_tempo_consulta(analise: Analise, regime: str) -> str:
    """CPU e tempo decorrido por consulta (metricas de teste exceto a do criterio): antes, depois, razao e IC."""
    crit = analise.criterios
    metricas = [m for m in crit.metricas_teste if m != crit.metrica_criterio]
    idx = _indice_comparacoes(analise)
    vs = _veredictos(analise, regime)
    flags = _flags(vs, antipadrao=False)
    linhas, tem_zero = [], False
    for v in vs:
        l = _prefixo(v, flags)
        for m in metricas:
            c = idx.get((v["experiment_id"], v["query_id"], v["tratamento_variant_id"], v["regime"], m))
            if c is None:
                l += [ND] * 3
                continue
            marca = "$^{*}$" if _cota(c) else ""
            tem_zero |= bool(marca)
            l += [tx(fmt_auto(c["mediana_antes"])) + " $\\to$ " + tx(fmt_auto(c["mediana_depois"])),
                  tx(fmt_sig(c["razao_reg"])) + marca, tx(fmt_ic(c["ic_lo"], c["ic_hi"]))]
        linhas.append(l)
    cab = _cab_prefixo(flags)
    grupos = []
    for m in metricas:
        grupos.append((ROTULO_METRICA_TEX.get(m, esc(m)), len(cab) + 1, 3))
        cab += ["antes $\\to$ depois", "Razão", _ic_rot(analise)]
    notas = [f"Regime {esc(regime)}. Medianas das repetições; razão = mediana depois/antes. O tempo decorrido não é comparável "
             "ao tempo de CPU quando o plano é paralelo."]
    if tem_zero:
        notas.append(f"$^{{*}}$ Mediana igual a zero (resolução de 1 ms): razão com piso de {tx(fmt_num(crit.piso_zero, 1))} (cota).")
    return _tabular("l" * len(_cab_prefixo(flags)) + "rrr" * len(metricas), cab, linhas, notas,
                    f"tab_tempo_consulta; regime {regime}; experimentos {analise.meta['experimentos']}; criterios_sha {crit.sha()}",
                    "\\footnotesize", grupos)


def tabela_efeito_antipadrao(analise: Analise, regime: str) -> str:
    """Efeito agregado por antipadrao (e geral): reducao mediana com IC entre consultas, Wilcoxon e Holm."""
    crit = analise.criterios
    ag = [a for a in analise.agregados if a["analise"] == "principal" and a["regime"] == regime
          and a["estrato_tipo"] in ("todas", "antipadrao")]
    if {a["estrato"] for a in ag if a["estrato_tipo"] == "antipadrao"} <= {SEM_ANTIPADRAO}:
        ag = [a for a in ag if a["estrato_tipo"] == "todas"]  # sem antipadrao conhecido, o estrato repetiria "Todas"
    ag.sort(key=lambda a: (a["grupo"], a["estrato_tipo"] != "todas", a["estrato"], crit.metricas_teste.index(a["metrica"])))
    mt = len({a["grupo"] for a in ag}) > 1
    cab = (["Tratamento"] if mt else []) + ["Estrato", "$n$", "Métrica", "Redução mediana", _ic_rot(analise) + " (bootstrap)",
                                           "$p$", "$p$ (Holm)", ""]
    linhas: list[list[str] | None] = []
    ultimo, explor = None, False
    for a in ag:
        chave = (a["grupo"], a["estrato_tipo"], a["estrato"])
        if ultimo is not None and chave != ultimo:
            linhas.append(None)
        ultimo = chave
        marca = a["poder_insuficiente"] or a["n_consultas"] < crit.n_min_consultas
        explor |= marca
        nome = "Todas" if a["estrato_tipo"] == "todas" else esc(a["estrato"])
        linhas.append(([esc(NOME_GRUPO.get(a["grupo"], a["grupo"]))] if mt else []) +
                      [nome, str(a["n_consultas"]), ROTULO_METRICA_TEX.get(a["metrica"], esc(a["metrica"])),
                       tx(fmt_pct(a["reducao_mediana_pct"])), tx(fmt_ic(a["reducao_ic_lo"], a["reducao_ic_hi"], lambda x: fmt_num(x, 1))),
                       tx(fmt_p(a["p_bruto"])), tx(fmt_p(a["p_holm"])), "$^{\\dagger}$" if marca else ""])
    notas = [f"Regime {esc(regime)}. Redução mediana = (1 $-$ mediana das razões depois/antes entre consultas) $\\times$ 100; $p$: "
             f"Wilcoxon de postos sinalizados sobre o log da razão ({ROTULO_ALTERNATIVA[crit.alternativa]}), Holm sobre "
             f"{len(crit.metricas_teste)} métricas, $\\alpha$ = {tx(fmt_num(crit.alfa, 2))}. Sem correção entre estratos."]
    if explor:
        notas.append(f"$^{{\\dagger}}$ Exploratório: menos de {crit.n_min_consultas} consultas ou $p$ mínimo atingível acima de "
                     "$\\alpha$ (o teste não pode rejeitar $H_0$).")
    return _tabular("l" * (1 if mt else 0) + "lrlrrrrl", cab, linhas, notas,
                    f"tab_efeito_antipadrao; regime {regime}; experimentos {analise.meta['experimentos']}; criterios_sha {crit.sha()}")


def tabela_sensibilidade(analise: Analise, regime: str) -> str:
    """Razao com todas as medidas e sem atipicos de Tukey; so as comparacoes com atipicos (as demais sao contadas)."""
    crit = analise.criterios
    cs = [c for c in analise.comparacoes if c["metrica"] in crit.metricas_teste and c["regime"] == regime
          and c["comparacao"] == "principal" and _principal(analise, c["papel"])]
    com = [c for c in cs if c["atipicos_antes"] or c["atipicos_depois"]]
    veredicto = {(v["experiment_id"], v["query_id"], v["tratamento_variant_id"], v["regime"]): v for v in analise.veredictos}
    mt = len({c["tratamento_kind"] for c in com}) > 1
    pre = 2 + (1 if mt else 0)
    cab = ["Consulta"] + (["Tratamento"] if mt else []) + ["Métrica", "Razão", _ic_rot(analise), "Razão", _ic_rot(analise),
                                                          "Atíp. A/D", "Crit. T/S"]
    grupos = [("Todas as medidas (T)", pre + 1, 2), ("Sem atípicos (S)", pre + 3, 2)]
    linhas, tem_zero = [], False
    for c in sorted(com, key=lambda c: (c["consulta"], c["tratamento_kind"], crit.metricas_teste.index(c["metrica"]))):
        v = veredicto.get((c["experiment_id"], c["query_id"], c["tratamento_variant_id"], c["regime"]))
        crt = ND
        if c["metrica"] == crit.metrica_criterio and v:
            crt = ("sim" if v["melhoria_significativa"] else "não") + "/" + ("sim" if v["melhoria_significativa_sem_atipicos"] else "não")
        mz = "$^{*}$" if (_cota(c) or _cota(c, "_sem_atipicos")) else ""
        tem_zero |= bool(mz)
        linhas.append([esc(c["consulta"])] + ([esc(c["tratamento_kind"])] if mt else []) +
                      [ROTULO_METRICA_TEX.get(c["metrica"], esc(c["metrica"])), tx(fmt_sig(c["razao_reg"])) + mz,
                       tx(fmt_ic(c["ic_lo"], c["ic_hi"])), tx(fmt_sig(c["razao_reg_sem_atipicos"])) + mz,
                       tx(fmt_ic(c["ic_lo_sem_atipicos"], c["ic_hi_sem_atipicos"])),
                       f"{c['atipicos_antes']}/{c['atipicos_depois']}", crt])
    if not linhas:
        linhas.append(["\\multicolumn{%d}{l}{Nenhum valor atípico (Tukey) sinalizado nas métricas testadas.}" % len(cab)])
    omitidas = len(cs) - len(com)
    notas = [f"Regime {esc(regime)}. Atípico: fora de [Q1 $-$ 1,5 IQR, Q3 + 1,5 IQR] no grupo (consulta, variante, regime, métrica); "
             "A/D = quantidade de atípicos antes/depois; T/S = critério de melhoria atendido com todas as medidas/sem atípicos. "
             "Nenhuma medida é descartada: as duas análises são reportadas." +
             (f" {omitidas} comparação(ões) sem atípicos omitida(s)." if omitidas else "")]
    if tem_zero:
        notas.append(f"$^{{*}}$ Mediana igual a zero: razão com piso de {tx(fmt_num(crit.piso_zero, 1))} (cota).")
    return _tabular("l" * (pre - 1) + "l" + "rrrrrc", cab, linhas, notas,
                    f"tab_sensibilidade; regime {regime}; experimentos {analise.meta['experimentos']}; criterios_sha {crit.sha()}",
                    "\\scriptsize", grupos)


def tabela_secundarias(analise: Analise, regime: str) -> str:
    """Bracos secundarios (reescrita, indice, nativo, contraexemplo, controle...) contra a baseline e o nativo contra o
    tratamento principal: leituras logicas, razao, IC. Fora do agregado principal (PRE-REGISTRO.md)."""
    crit = analise.criterios
    cs = sorted((c for c in analise.comparacoes if c["regime"] == regime and c["metrica"] == crit.metrica_criterio
                 and c["comparacao"] != "principal"), key=lambda c: (c["consulta"], c["comparacao"], c["tratamento_kind"]))
    linhas, tem_zero = [], False
    for c in cs:
        marca = "$^{*}$" if _cota(c) else ""
        tem_zero |= bool(marca)
        contra = "original" if c["comparacao"] == "secundaria" else "prescrição"
        linhas.append([esc(_nome(analise, c)), esc(c["tratamento_kind"]), contra, tx(fmt_auto(c["mediana_antes"])), tx(fmt_auto(c["mediana_depois"])),
                       tx(fmt_sig(c["razao_reg"])) + marca, tx(fmt_ic(c["ic_lo"], c["ic_hi"])), tx(fmt_pct(c["reducao_pct"]))])
    if not linhas:
        linhas.append(["\\multicolumn{8}{l}{Nenhum braço secundário medido.}"])
    notas = [f"Regime {esc(regime)}. Mediana de {esc(ROTULO_METRICA[crit.metrica_criterio].lower())}; razão = mediana depois/antes, "
             "em que `antes' é a linha de base indicada (original ou o tratamento principal do framework). Comparações secundárias, "
             "descritivas, fora do agregado principal."]
    longos = sorted({(c["tratamento_kind"], c["tratamento"]) for c in cs if c["tratamento"] != c["tratamento_kind"] and len(c["tratamento"]) > 16})
    if longos:
        notas.append("Braços: " + "; ".join(f"{esc(k)} = {esc(n)}" for k, n in longos) + ".")
    if tem_zero:
        notas.append(f"$^{{*}}$ Mediana igual a zero: razão com piso de {tx(fmt_num(crit.piso_zero, 1))} (cota).")
    return _tabular("llllrrrrr"[:8], ["Consulta", "Braço", "Contra", "Antes", "Depois", "Razão", _ic_rot(analise), "Redução"], linhas, notas,
                    f"tab_secundarias; regime {regime}; experimentos {analise.meta['experimentos']}; criterios_sha {crit.sha()}",
                    "\\footnotesize")


def tabela_escrita(analise: Analise) -> str:
    """Custo de escrita do indice da prescricao: razao com/sem indice por operacao, em leituras logicas e bytes de log.
    SEM limiar de aceitacao (PRE-REGISTRO.md)."""
    crit = analise.criterios
    por: dict[tuple, dict[str, dict]] = {}
    for e in analise.escrita:
        if e["metrica"] in ("logical_reads", "log_bytes"):
            por.setdefault((e["consulta"], e["operacao"], e["indice"]), {})[e["metrica"]] = e
    ordem_op = {"insert": 0, "update": 1, "delete": 2}
    linhas = []
    for (cons, op, indice), m in sorted(por.items(), key=lambda kv: (kv[0][0], ordem_op.get(kv[0][1], 9))):
        def cel(chave):
            e = m.get(chave)
            return [ND, ND, ND] if e is None else [tx(fmt_auto(e["mediana_sem"])), tx(fmt_auto(e["mediana_com"])), tx(fmt_sig(e["razao_reg"]))]
        linhas.append([esc(cons), esc(op.upper())] + cel("logical_reads") + cel("log_bytes"))
    if not linhas:
        linhas.append(["\\multicolumn{8}{l}{Custo de escrita não medido nesta coleta.}"])
    grupos = [("Leituras lógicas", 3, 3), ("Bytes de log", 6, 3)]
    notas = ["Mediana das repetições de cada operação (k linhas, dentro de transação revertida), sem e com o índice da prescrição; "
             "razão = com/sem. Sem limiar de aceitação: o custo é reportado, não julgado."]
    return _tabular("llrrrrrr", ["Consulta", "Operação", "sem", "com", "razão", "sem", "com", "razão"], linhas, notas,
                    f"tab_escrita; experimentos {analise.meta['experimentos']}; criterios_sha {crit.sha()}", "\\small", grupos)


TABELAS = {"tab_resumo_consulta": tabela_resumo_consulta, "tab_tempo_consulta": tabela_tempo_consulta,
           "tab_efeito_antipadrao": tabela_efeito_antipadrao, "tab_sensibilidade": tabela_sensibilidade}


def escrever_tabelas(analise: Analise, pasta: str | Path) -> list[Path]:
    """Uma tabela de cada tipo por regime: tab_<tipo>_<regime>.tex (nomes estaveis para o \\input do artigo)."""
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    saida = []
    for regime in regimes_da_analise(analise):
        for nome, f in TABELAS.items():
            caminho = pasta / f"{nome}_{regime}.tex"
            caminho.write_text(f(analise, regime), encoding="utf-8")
            saida.append(caminho)
        if _veredictos(analise, regime, controles=True):           # controles: tabela propria (so se existirem)
            caminho = pasta / f"tab_controles_{regime}.tex"
            caminho.write_text(tabela_resumo_consulta(analise, regime, controles=True), encoding="utf-8")
            saida.append(caminho)
        if any(c["regime"] == regime and c["comparacao"] != "principal" for c in analise.comparacoes):   # bracos secundarios
            caminho = pasta / f"tab_secundarias_{regime}.tex"
            caminho.write_text(tabela_secundarias(analise, regime), encoding="utf-8")
            saida.append(caminho)
    if analise.escrita:                                              # custo de escrita: uma tabela por analise
        caminho = pasta / "tab_escrita.tex"
        caminho.write_text(tabela_escrita(analise), encoding="utf-8")
        saida.append(caminho)
    return saida


# ----------------------------------------------------------------------------------------- figuras
def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"pdf.fonttype": 42, "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8,
                         "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
                         "axes.grid": True, "grid.alpha": 0.25, "grid.linewidth": 0.5})
    return plt


def _salvar(fig, caminho: Path) -> Path:
    fig.savefig(caminho, format="pdf", bbox_inches="tight", metadata={"CreationDate": None, "Creator": "tcclab.relatorio"})
    import matplotlib.pyplot as plt

    plt.close(fig)
    return caminho


def _consultas_ordenadas(analise: Analise, itens: list[dict]) -> list[str]:
    visto: dict[str, tuple] = {}
    for i in itens:
        visto.setdefault(_nome(analise, i), (i["antipadrao"], i["consulta"]))
    return [k for k, _ in sorted(visto.items(), key=lambda kv: kv[1])]


def figura_razao(analise: Analise, regime: str, caminho: str | Path) -> Path | None:
    """Razao depois/antes por consulta (escala log), IC bootstrap, uma coluna por metrica testada."""
    crit = analise.criterios
    cs = [c for c in analise.comparacoes if c["regime"] == regime and c["metrica"] in crit.metricas_teste
          and c["comparacao"] == "principal"]
    if not cs:
        return None
    plt = _plt()
    consultas = _consultas_ordenadas(analise, cs)
    kinds = sorted({c["tratamento_kind"] for c in cs})
    fig, eixos = plt.subplots(1, len(crit.metricas_teste), sharey=True,
                              figsize=(6.3, max(2.0, 0.3 * len(consultas) * max(1, len(kinds)) + 1.1)), squeeze=False)
    tem_cota = False
    for ax, metrica in zip(eixos[0], crit.metricas_teste):
        for c in (c for c in cs if c["metrica"] == metrica):
            j = kinds.index(c["tratamento_kind"])
            y = consultas.index(_nome(analise, c)) + (j - (len(kinds) - 1) / 2) * 0.22
            r = c["razao_reg"]
            cota = c["regra_zero"] not in ("", "ambas_zero")   # mediana zero: razao = cota (piso) -> marcador vazado
            tem_cota |= cota
            ax.errorbar(r, y, xerr=[[max(0.0, r - c["ic_lo"])], [max(0.0, c["ic_hi"] - r)]], fmt=MARCADORES[j % len(MARCADORES)],
                        color=PALETA_TRAT[j % len(PALETA_TRAT)], ms=4, capsize=2, lw=1, mfc="none" if cota else None,
                        label=c["tratamento_kind"])
        ax.set_xscale("log")
        ax.axvline(1.0, color="0.3", lw=0.8)
        if metrica == crit.metrica_criterio:
            ax.axvline(1 - crit.reducao_minima_pct / 100, color="0.3", lw=0.8, ls="--")
        ax.set_title(ROTULO_METRICA[metrica])
        ax.set_xlabel("razão depois/antes (mediana)")
    eixos[0][0].set_yticks(range(len(consultas)), consultas)
    eixos[0][0].invert_yaxis()
    h, l = [], []
    for ax in eixos[0]:
        for hh, ll in zip(*ax.get_legend_handles_labels()):
            if ll not in l:       # um item por tratamento (cada consulta repetiria o rotulo)
                h.append(hh)
                l.append(ll)
    if tem_cota:
        h.append(eixos[0][0].plot([], [], "o", mfc="none", color="0.3", ms=4)[0])
        l.append("marcador vazado: mediana 0, razão é cota (piso)")
    fig.tight_layout()
    if h:
        fig.legend(h, l, title="tratamento", loc="upper center", bbox_to_anchor=(0.5, 0.0), ncol=len(l), fontsize=7)
    fig.suptitle(f"Razão depois/antes por consulta, regime {regime} (barras: {_ic_texto(crit)}; "
                 f"tracejado: redução de {fmt_num(crit.reducao_minima_pct)}%)", fontsize=8, y=1.0, va="bottom")
    return _salvar(fig, Path(caminho))


def _ic_texto(crit) -> str:
    return f"IC{round(crit.nivel_ic * 100):d}% bootstrap"


def figura_leituras(analise: Analise, regime: str, caminho: str | Path) -> Path | None:
    """Mediana de leituras logicas antes/depois por consulta, barras em escala log."""
    crit = analise.criterios
    vs = [v for v in analise.veredictos if v["regime"] == regime and v["comparacao"] == "principal"]
    if not vs:
        return None
    plt = _plt()
    consultas = _consultas_ordenadas(analise, vs)
    kinds = sorted({v["tratamento_kind"] for v in vs})
    larg = 0.8 / (1 + len(kinds))
    fig, ax = plt.subplots(figsize=(6.3, 3.0))
    piso = crit.piso_zero
    topos = []
    base_feita, rotulados = set(), set()
    for v in vs:
        i = consultas.index(_nome(analise, v))
        j = kinds.index(v["tratamento_kind"])
        if v["consulta"] not in base_feita:
            base_feita.add(v["consulta"])
            b = max(v["mediana_antes"], piso)
            ax.bar(i - 0.4 + larg / 2, b, larg, color=PALETA_BASE, hatch="//", edgecolor="white", label="antes (original)" if not topos else None)
            topos.append(b)
            ax.text(i - 0.4 + larg / 2, b, fmt_auto(v["mediana_antes"]), ha="center", va="bottom", fontsize=6, rotation=90)
        d = max(v["mediana_depois"], piso)
        ax.bar(i - 0.4 + larg * (1.5 + j), d, larg, color=PALETA_TRAT[j % len(PALETA_TRAT)], edgecolor="white",
               label=f"depois ({v['tratamento_kind']})" if v["tratamento_kind"] not in rotulados else None)
        rotulados.add(v["tratamento_kind"])
        topos.append(d)
        ax.text(i - 0.4 + larg * (1.5 + j), d, fmt_auto(v["mediana_depois"]), ha="center", va="bottom", fontsize=6, rotation=90)
    ax.set_yscale("log")
    ax.set_ylim(piso / 2, max(topos) * 150)   # folga para os rotulos rotacionados e a legenda
    ax.set_xticks(range(len(consultas)), consultas, rotation=45 if len(consultas) > 6 else 0, ha="right" if len(consultas) > 6 else "center")
    ax.set_ylabel("leituras lógicas (mediana, escala log)")
    ax.set_title(f"Leituras lógicas antes e depois, regime {regime}")
    ax.legend(fontsize=7, ncol=1 + len(kinds), loc="upper right")
    ax.grid(axis="x", visible=False)
    return _salvar(fig, Path(caminho))


def figura_repeticoes(analise: Analise, regime: str, familia: str, caminho: str | Path) -> Path | None:
    """Caixa (Tukey) e pontos das repeticoes de CPU ou tempo decorrido; atipicos marcados com x, nunca ocultados."""
    crit = analise.criterios
    plt = _plt()
    chaves = sorted({(g.experiment_id, g.query_id) for g in analise.grupos.values() if g.regime == regime})
    if not chaves:
        return None
    rot = {(c["experiment_id"], c["query_id"]): _nome(analise, c) for c in analise.consultas}
    ncol = min(4, len(chaves))
    nlin = math.ceil(len(chaves) / ncol)
    fig, eixos = plt.subplots(nlin, ncol, figsize=(6.3, 2.0 * nlin + 0.3), squeeze=False)
    rng = np.random.default_rng([crit.semente, 7])  # dispersao horizontal fixa -> PDF reprodutivel
    usadas: set[str] = set()
    for ax, (exp, qid) in zip(eixos.flat, chaves):
        gs = sorted((g for g in analise.grupos.values() if (g.experiment_id, g.query_id) == (exp, qid) and g.regime == regime),
                    key=lambda g: (g.kind != crit.kind_baseline, g.variant_id))
        us, ms = f"{familia}_us", f"{familia}_ms"
        metrica = us if us in crit.metricas_resumo and all(len(g.vetor(us)) for g in gs) else ms
        usadas.add(metrica)
        dados = [g.vetor(metrica) for g in gs]
        if not any(len(d) for d in dados):
            ax.set_visible(False)
            continue
        ax.boxplot(dados, positions=range(1, len(gs) + 1), widths=0.5, whis=1.5, showfliers=False,
                   medianprops={"color": "black"}, boxprops={"color": "0.35"}, whiskerprops={"color": "0.35"}, capprops={"color": "0.35"})
        for k, g in enumerate(gs):
            pares = g.pares(metrica)
            marc = set(resumir([p[2] for p in pares]).atipicos) if pares else set()
            cor = PALETA_BASE if g.kind == crit.kind_baseline else PALETA_TRAT[(k - 1) % len(PALETA_TRAT)]
            xs = k + 1 + rng.uniform(-0.18, 0.18, len(pares))
            ys = np.array([p[2] for p in pares])
            norm = np.array([i not in marc for i in range(len(pares))], dtype=bool)
            ax.scatter(xs[norm], ys[norm], s=6, color=cor, alpha=0.7, linewidths=0)
            ax.scatter(xs[~norm], ys[~norm], s=14, color=cor, marker="x", linewidths=0.9)
        todos = np.concatenate([d for d in dados if len(d)])
        ax.set_yscale("log" if todos.min() > 0 else "symlog", **({} if todos.min() > 0 else {"linthresh": 1}))
        ax.set_xticks(range(1, len(gs) + 1), [g.variante if len(g.variante) <= 16 else g.kind for g in gs], rotation=30, ha="right", fontsize=6)
        ax.set_title(rot[(exp, qid)], fontsize=8)
    for ax in list(eixos.flat)[len(chaves):]:
        ax.set_visible(False)
    unidade = "µs" if usadas == {f"{familia}_us"} else "ms; escala simlog se houver zeros" if usadas == {f"{familia}_ms"} else "µs ou ms"
    nome = "CPU" if familia == "cpu" else "Tempo decorrido"
    fig.supylabel(f"{nome} por repetição ({unidade})", fontsize=8)
    fig.tight_layout()
    fig.suptitle(f"{nome}: repetições por variante, regime {regime} (x = atípico de Tukey)", fontsize=8, y=1.0, va="bottom")
    return _salvar(fig, Path(caminho))


def escrever_figuras(analise: Analise, pasta: str | Path) -> list[Path]:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    saida = []
    for regime in sorted({c["regime"] for c in analise.comparacoes}):
        for p in (figura_razao(analise, regime, pasta / f"razao_por_consulta_{regime}.pdf"),
                  figura_leituras(analise, regime, pasta / f"leituras_antes_depois_{regime}.pdf"),
                  figura_repeticoes(analise, regime, "cpu", pasta / f"repeticoes_cpu_{regime}.pdf"),
                  figura_repeticoes(analise, regime, "elapsed", pasta / f"repeticoes_decorrido_{regime}.pdf")):
            if p:
                saida.append(p)
    return saida


# ----------------------------------------------------------------------------------------- numeros do artigo
def _ids(texto: str) -> list[int]:
    return sorted({int(x) for x in texto.split(";") if x})


def numeros_do_artigo(analise: Analise) -> list[dict]:
    """Cada numero reportavel -> valor, texto pt-BR, experiment_id(s) e run_id(s) de origem, tabela de origem."""
    crit = analise.criterios
    itens: list[dict] = []

    def add(id_, rotulo, valor, texto, exps, runs, arquivo, destaque=False):
        itens.append({"id": id_, "rotulo": rotulo, "valor": valor if _ok(valor) or isinstance(valor, (int, str)) else None,
                      "texto": texto, "experiment_ids": exps, "run_ids": runs, "fonte": arquivo, "destaque": destaque})

    campos_cmp = [("mediana_antes", "mediana antes", fmt_auto), ("mediana_depois", "mediana depois", fmt_auto),
                  ("razao_reg", "razão depois/antes", fmt_sig), ("ic_lo", "limite inferior do IC da razão", fmt_sig),
                  ("ic_hi", "limite superior do IC da razão", fmt_sig), ("reducao_pct", "redução (%)", fmt_pct),
                  ("razao_reg_sem_atipicos", "razão sem atípicos", fmt_sig), ("reducao_pct_sem_atipicos", "redução (%) sem atípicos", fmt_pct),
                  ("n_antes", "n de repetições antes", lambda x: str(int(x))), ("n_depois", "n de repetições depois", lambda x: str(int(x))),
                  ("atipicos_antes", "atípicos antes", lambda x: str(int(x))), ("atipicos_depois", "atípicos depois", lambda x: str(int(x)))]
    for c in analise.comparacoes:
        if c["metrica"] not in crit.metricas_teste:
            continue
        runs = _ids(c["run_ids_antes"] + ";" + c["run_ids_depois"])
        prefixo = "" if c["comparacao"] == "principal" else f"{c['comparacao']}:"
        for campo, rot, f in campos_cmp:
            add(f"c:{c['consulta']}:{prefixo}{c['tratamento_kind']}:{c['regime']}:{c['metrica']}:{campo}",
                f"{c['consulta']} / {c['tratamento_kind']} ({c['comparacao']}) / {c['regime']} / {ROTULO_METRICA[c['metrica']]}: {rot}",
                c[campo], f(c[campo]) if c[campo] is not None else ND, [c["experiment_id"]], runs, "comparisons.csv",
                destaque=c["metrica"] == crit.metrica_criterio and c["comparacao"] == "principal" and c["regime"] == crit.regime_principal)
    for a in analise.agregados:
        runs = _ids(a["run_ids"])
        exps = _ids(a["experimentos"])
        base = f"a:{a['analise']}:{a['regime']}:{a['grupo']}:{a['estrato_tipo']}={a['estrato']}:{a['metrica']}"
        quem = f"{a['analise']} / {a['regime']} / {a['grupo']} / {a['estrato']} / {ROTULO_METRICA[a['metrica']]}"
        for campo, rot, f in (("n_consultas", "n de consultas", lambda x: str(int(x))),
                              ("reducao_mediana_pct", "redução mediana (%)", fmt_pct),
                              ("reducao_ic_lo", "limite inferior do IC da redução mediana", lambda x: fmt_num(x, 1)),
                              ("reducao_ic_hi", "limite superior do IC da redução mediana", lambda x: fmt_num(x, 1)),
                              ("razao_mediana", "mediana das razões", fmt_sig), ("p_bruto", "p (Wilcoxon)", fmt_p),
                              ("p_holm", "p (Holm)", fmt_p), ("p_minimo", "p mínimo atingível", fmt_p),
                              ("n_melhoria_significativa", "consultas com melhoria significativa", lambda x: str(int(x)))):
            if campo in a and a[campo] is not None:
                add(f"{base}:{campo}", f"{quem}: {rot}", a[campo], f(a[campo]), exps, runs, "aggregate.csv",
                    destaque=a["analise"] == "principal" and a["regime"] == crit.regime_principal)
    for e in analise.escrita:   # custo de escrita: sem run_id (escrita_medicao nao tem), origem = rodada + query_id da tabela
        if e["metrica"] not in ("logical_reads", "log_bytes"):
            continue
        base = f"e:{e['consulta']}:{e['operacao']}:{e['metrica']}"
        for campo, rot, f in (("mediana_sem", "mediana sem índice", fmt_auto), ("mediana_com", "mediana com índice", fmt_auto),
                              ("razao", "razão com/sem índice", fmt_sig), ("aumento_pct", "aumento (%)", fmt_pct)):
            if e[campo] is not None:
                itens.append({"id": f"{base}:{campo}", "rotulo": f"{e['consulta']} / {e['operacao']} / {e['indice']} / "
                              f"{ROTULO_METRICA.get(e['metrica'], e['metrica'])}: {rot}", "valor": e[campo], "texto": f(e[campo]),
                              "experiment_ids": [e["experiment_id"]], "run_ids": [], "fonte": "escrita.csv",
                              "origem_sqlite": f"escrita_medicao (rodada {e['rodada']}, query_id {e['query_id']})", "destaque": True})
    for d in analise.determinismo:
        add(f"d:{d['consulta']}:{d['variante']}:{d['regime']}:logical_reads:dp", f"{d['consulta']} / {d['variante']} / {d['regime']}: "
            "desvio padrão das leituras lógicas", d["dp"], fmt_sig(d["dp"]) if d["dp"] else "0", [d["experiment_id"]], _ids(d["run_ids"]),
            "determinism.csv", destaque=True)
    return itens


def escrever_numeros(analise: Analise, pasta: str | Path) -> list[Path]:
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    itens = numeros_do_artigo(analise)
    cab = {"gerado_em_utc": analise.meta["gerado_em_utc"], "banco": analise.meta["banco"], "banco_sha256": analise.meta["banco_sha256"],
           "experimentos": analise.meta["experimentos"], "criterios_sha": analise.criterios.sha(),
           "aviso": "Nenhum numero do artigo deve ser digitado a mao: use o id abaixo para localizar a origem.", "numeros": itens}
    caminho_json = pasta / "numeros-do-artigo.json"
    caminho_json.write_text(json.dumps(json_seguro(cab), ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# Números do artigo", "", f"Banco `{cab['banco']}` (sha256 `{cab['banco_sha256'][:16]}`), experimentos {cab['experimentos']}, "
          f"critérios `{cab['criterios_sha']}`. Lista completa em `numeros-do-artigo.json`; abaixo só os destaques.", "",
          "| id | rótulo | texto | experimento(s) | run_id(s) | fonte |", "|---|---|---|---|---|---|"]
    for i in (i for i in itens if i["destaque"]):
        md.append(f"| `{i['id']}` | {i['rotulo']} | {i['texto']} | {', '.join(map(str, i['experiment_ids']))} | "
                  f"{', '.join(map(str, i['run_ids']))} | {i['fonte']} |")
    caminho_md = pasta / "numeros-do-artigo.md"
    caminho_md.write_text("\n".join(md) + "\n", encoding="utf-8")
    return [caminho_json, caminho_md]


# ----------------------------------------------------------------------------------------- saida de texto e orquestracao
def relatorio_texto(analise: Analise) -> str:
    crit = analise.criterios
    out = [f"experimentos {analise.meta['experimentos']}  criterios {crit.sha()}  semente {crit.semente}  "
           f"reamostragens {crit.reamostragens}  alfa {crit.alfa}  regime principal {crit.regime_principal}"]
    for a in analise.avisos:
        out.append(f"AVISO: {a}")
    for d in analise.descartados:
        out.append(f"DESCARTADO (deriva): experimento {d['experiment_id']} ({d['query_id']}, tentativa {d['tentativa']}, "
                   f"deriva A1->A2 {fmt_pct((d['deriva_a1_a2'] or 0) * 100)})")
    cab = f"{'consulta':16} {'regime':7} {'braco':10} {'antes':>9} {'depois':>9} {'razao':>9} {'IC':>22} {'reducao':>9}  criterio"

    def linha(v):
        return (f"{_nome(analise, v)[:16]:16} {v['regime']:7} {v['tratamento_kind'][:10]:10} {fmt_auto(v['mediana_antes']):>9} "
                f"{fmt_auto(v['mediana_depois']):>9} {fmt_sig(v['razao_reg']):>9} {fmt_ic(v['ic_lo'], v['ic_hi']):>22} "
                f"{fmt_pct(v['reducao_pct']):>9}  {'SIM' if v['melhoria_significativa'] else 'nao'}"
                f"{' piora' if v['piora'] else ''}")
    ordem = lambda v: (v["regime"] != crit.regime_principal, v["regime"], v["consulta"], v["tratamento_kind"])  # noqa: E731
    out += ["", "tratamento principal (prescricao do framework) contra a linha de base:", cab]
    out += [linha(v) for v in sorted((v for v in analise.veredictos if v["comparacao"] == "principal"), key=ordem)]
    sec = sorted((v for v in analise.veredictos if v["comparacao"] != "principal"), key=ordem)
    if sec:
        out += ["", "bracos secundarios contra a linha de base (fora do agregado principal):", cab] + [linha(v) for v in sec]
    out += ["", f"{'analise':13} {'regime':7} {'grupo':8} {'estrato':18} {'metrica':14} {'n':>3} {'reducao med.':>12} {'p':>7} {'p Holm':>7}  metodo / observacao"]
    for a in sorted(analise.agregados, key=lambda a: (a["analise"] != "principal", a["analise"], a["regime"] != crit.regime_principal, a["regime"])):
        if a["analise"] == "sem_atipicos" or a["estrato_tipo"] == "seletividade" or (a["analise"] != "principal" and a["estrato_tipo"] != "todas"):
            continue
        out.append(f"{a['analise'][:13]:13} {a['regime']:7} {a['grupo'][:8]:8} {a['estrato'][:18]:18} {a['metrica']:14} {a['n_consultas']:>3} "
                   f"{fmt_pct(a['reducao_mediana_pct']):>12} {fmt_p(a['p_bruto']):>7} {fmt_p(a['p_holm']):>7}  {a['metodo_p']}"
                   f"{'; ' + a['observacao'] if a['observacao'] else ''}")
    if analise.escrita:
        out += ["", "custo de escrita (razao com/sem indice, mediana; sem limiar):"]
        for e in analise.escrita:
            if e["metrica"] == "logical_reads":
                out.append(f"  {e['consulta']:10} {e['operacao']:7} {e['indice'][:28]:28} leituras logicas {fmt_auto(e['mediana_sem'])} -> "
                           f"{fmt_auto(e['mediana_com'])} (x{fmt_sig(e['razao_reg'])})")
    nd = [d for d in analise.determinismo if not d["deterministico"]]
    out.append("")
    out.append(f"determinismo das leituras logicas: {len(analise.determinismo) - len(nd)}/{len(analise.determinismo)} grupos com dp = 0"
               + ("".join(f"\n  {d['consulta']}/{d['variante']}/{d['regime']}: {d['alerta']}" for d in nd)))
    out.append(f"atipicos de Tukey sinalizados (nunca descartados): {len(analise.atipicos)} medidas em outliers.csv")
    return "\n".join(out)


def gerar_relatorio(analise: Analise, pasta: str | Path, figuras: bool = True) -> list[Path]:
    """Escreve CSV, JSON, tabelas .tex, figuras .pdf e numeros-do-artigo em `pasta`."""
    pasta = Path(pasta)
    arquivos = escrever_csv(analise, pasta) + escrever_json(analise, pasta)
    arquivos += escrever_tabelas(analise, pasta / "tabelas")
    if figuras:
        arquivos += escrever_figuras(analise, pasta / "figuras")
    arquivos += escrever_numeros(analise, pasta)
    return arquivos
