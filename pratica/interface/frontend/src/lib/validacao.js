// Transformacoes puras sobre a resposta de /validacao. Nenhuma estatistica nova: so selecao e rotulagem.
export const METRICAS_PRINCIPAIS = ['logical_reads', 'cpu_ms', 'elapsed_ms'];
export const METRICAS_SECUNDARIAS = ['cpu_us', 'elapsed_us', 'physical_reads', 'read_ahead_reads'];

export function variantesDe(validacao) {
  return validacao?.variantes ?? [];
}

export function baseline(validacao) {
  return variantesDe(validacao).find((v) => v.kind === 'original') ?? null;
}

export function tratamentos(validacao) {
  return variantesDe(validacao).filter((v) => v.kind !== 'original');
}

export function achar(lista, pred) {
  return (lista ?? []).find(pred) ?? null;
}

export function origemDoGrupo(g, rodada, experimentId) {
  if (!g) return null;
  return { rodada, experiment_id: experimentId, run_ids: g.run_ids, tabela: 'measurement', metrica: g.metrica, n: g.n, fonte: g.fonte };
}

/**
 * Linhas da tabela antes x depois para um regime. `depois` e o NOME da variante de tratamento.
 * Cada linha: { metrica, antes, depois, comp, secundaria }. Sem grupos para o regime => [].
 */
export function linhasComparacao(validacao, regime, depois) {
  if (!validacao?.disponivel) return [];
  const base = baseline(validacao);
  if (!base) return [];
  const grupos = validacao.grupos ?? [];
  const existe = [...METRICAS_PRINCIPAIS, ...METRICAS_SECUNDARIAS].filter((m) =>
    grupos.some((g) => g.regime === regime && g.metrica === m && (g.variante === base.nome || g.variante === depois)),
  );
  return existe.map((metrica) => ({
    metrica,
    secundaria: !METRICAS_PRINCIPAIS.includes(metrica),
    antes: achar(grupos, (g) => g.regime === regime && g.metrica === metrica && g.variante === base.nome),
    depois: achar(grupos, (g) => g.regime === regime && g.metrica === metrica && g.variante === depois),
    comp: acharComparacao(validacao, base.nome, regime, metrica, depois),
  }));
}

/** Comparacao "depois contra a baseline" (ignora a comparacao nativo x prescricao, cuja baseline e outra). */
export function acharComparacao(validacao, baselineNome, regime, metrica, depois) {
  return achar(
    validacao.comparacoes,
    (c) => c.regime === regime && c.metrica === metrica && c.tratamento === depois && (c.baseline === undefined || c.baseline === baselineNome),
  );
}

export function acharVeredito(validacao, regime, depois) {
  return achar(validacao?.veredictos, (v) => v.regime === regime && v.tratamento === depois && v.comparacao !== 'nativo_vs_prescricao');
}

/** Limite de razao equivalente a reducao minima do criterio: 20 % => 0,80. Vem de Criterios.reducao_minima_pct. */
export function limiteDeRazao(criterios) {
  const r = criterios?.reducao_minima_pct;
  return typeof r === 'number' && Number.isFinite(r) ? 1 - r / 100 : null;
}

export function regimesDisponiveis(validacao) {
  const r = new Set((validacao?.grupos ?? []).map((g) => g.regime));
  return ['quente', 'frio'].filter((x) => r.has(x));
}

/** Variacao percentual da mediana (negativa = reducao), a partir da reducao ja calculada por tcclab.analise. */
export function variacaoPct(comp) {
  if (!comp || comp.reducao_pct === null || comp.reducao_pct === undefined) return null;
  return -comp.reducao_pct;
}

export function notaRegraZero(comp) {
  switch (comp?.regra_zero) {
    case 'depois_zero':
      return 'Mediana depois = 0 (resolução da medida): razão 0; o IC usa o piso de tcclab.analise (cota superior da razão verdadeira).';
    case 'antes_zero':
      return 'Mediana antes = 0: razão bruta indefinida; mostrada a razão regularizada (cota inferior).';
    case 'ambas_zero':
      return 'Medianas antes e depois = 0: sem mudança detectável na resolução da medida (razão 1).';
    default:
      return null;
  }
}

/** Itens do grafico de razao (um por metrica principal) para um regime. */
export function itensRazao(linhas, rodada, experimentId, rotulo) {
  return linhas
    .filter((l) => !l.secundaria)
    .map((l) => ({
      rotulo: rotulo(l.metrica),
      razao: l.comp?.razao ?? null,
      razao_reg: l.comp?.razao_reg ?? null,
      ic_lo: l.comp?.ic_lo ?? null,
      ic_hi: l.comp?.ic_hi ?? null,
      origem: `${rodada} · exp ${experimentId} · runs ${[...(l.comp?.run_ids_antes ?? []), ...(l.comp?.run_ids_depois ?? [])].join(', ')}`,
    }));
}
