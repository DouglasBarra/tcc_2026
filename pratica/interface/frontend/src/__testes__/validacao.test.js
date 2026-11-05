// Dados SINTETICOS: existem so neste arquivo de teste e nunca na interface.
import { describe, expect, it } from 'vitest';
import { acharVeredito, itensRazao, limiteDeRazao, linhasComparacao, notaRegraZero, regimesDisponiveis, tratamentos, variacaoPct } from '../lib/validacao.js';

const grupo = (variante, regime, metrica, mediana) => ({ variante, regime, metrica, mediana, q1: mediana, q3: mediana, n: 5, run_ids: [1], fonte: 'teste' });
const SINT = {
  disponivel: true,
  variantes: [{ nome: 'original', kind: 'original' }, { nome: 'reescrita+indice', kind: 'prescricao' }, { nome: 'nativo', kind: 'nativo' }],
  grupos: [
    grupo('original', 'quente', 'logical_reads', 1000), grupo('reescrita+indice', 'quente', 'logical_reads', 50), grupo('nativo', 'quente', 'logical_reads', 90),
    grupo('original', 'quente', 'cpu_ms', 20), grupo('reescrita+indice', 'quente', 'cpu_ms', 2),
    grupo('original', 'frio', 'logical_reads', 1000), grupo('reescrita+indice', 'frio', 'logical_reads', 50),
  ],
  comparacoes: [
    { regime: 'quente', metrica: 'logical_reads', baseline: 'original', tratamento: 'reescrita+indice', razao: 0.05, reducao_pct: 95, comparacao: 'principal', run_ids_antes: [1], run_ids_depois: [2] },
    { regime: 'quente', metrica: 'logical_reads', baseline: 'reescrita+indice', tratamento: 'nativo', razao: 1.8, comparacao: 'nativo_vs_prescricao', run_ids_antes: [2], run_ids_depois: [3] },
    { regime: 'quente', metrica: 'logical_reads', baseline: 'original', tratamento: 'nativo', razao: 0.09, reducao_pct: 91, comparacao: 'secundaria', run_ids_antes: [1], run_ids_depois: [3] },
  ],
  veredictos: [
    { regime: 'quente', tratamento: 'nativo', comparacao: 'nativo_vs_prescricao', melhoria_significativa: false },
    { regime: 'quente', tratamento: 'nativo', comparacao: 'secundaria', melhoria_significativa: true },
  ],
};

describe('selecao de dados da Validacao', () => {
  it('so lista tratamentos (variantes que nao sao a original)', () => {
    expect(tratamentos(SINT).map((v) => v.nome)).toEqual(['reescrita+indice', 'nativo']);
  });

  it('regimes vem dos grupos e o frio fica separado do quente', () => {
    expect(regimesDisponiveis(SINT)).toEqual(['quente', 'frio']);
    expect(linhasComparacao(SINT, 'frio', 'reescrita+indice').map((l) => l.metrica)).toEqual(['logical_reads']);
    expect(linhasComparacao(SINT, 'quente', 'reescrita+indice').map((l) => l.metrica)).toEqual(['logical_reads', 'cpu_ms']);
  });

  it('nao confunde a comparacao nativo x prescricao com nativo x original', () => {
    const l = linhasComparacao(SINT, 'quente', 'nativo')[0];
    expect(l.comp.razao).toBe(0.09);
    expect(acharVeredito(SINT, 'quente', 'nativo').comparacao).toBe('secundaria');
  });

  it('sem comparacao do modulo de analise a linha existe, mas sem razao inventada', () => {
    const l = linhasComparacao(SINT, 'quente', 'reescrita+indice')[1]; // cpu_ms nao tem comparacao
    expect(l.comp).toBeNull();
    expect(variacaoPct(l.comp)).toBeNull();
  });

  it('sem grupos para o regime nao ha linhas (estado vazio, nao zeros)', () => {
    expect(linhasComparacao({ ...SINT, grupos: [] }, 'quente', 'nativo')).toEqual([]);
    expect(linhasComparacao({ disponivel: false }, 'quente', 'nativo')).toEqual([]);
  });

  it('variacao e o oposto da reducao e o limite da razao vem do criterio', () => {
    expect(variacaoPct({ reducao_pct: 95 })).toBe(-95);
    expect(limiteDeRazao({ reducao_minima_pct: 20 })).toBeCloseTo(0.8);
    expect(limiteDeRazao(null)).toBeNull();
    expect(limiteDeRazao({})).toBeNull();
  });

  it('notas das regras de zero', () => {
    expect(notaRegraZero({ regra_zero: 'depois_zero' })).toMatch(/piso/);
    expect(notaRegraZero({ regra_zero: '' })).toBeNull();
    expect(notaRegraZero(null)).toBeNull();
  });

  it('itens do grafico de razao carregam a origem (runs) de cada comparacao', () => {
    const itens = itensRazao(linhasComparacao(SINT, 'quente', 'reescrita+indice'), 'r1', 4, (m) => m);
    expect(itens[0].origem).toContain('runs 1, 2');
    expect(itens[1].razao).toBeNull(); // sem comparacao: o grafico lista como indisponivel
  });
});
