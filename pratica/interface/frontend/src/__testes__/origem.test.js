import { describe, expect, it } from 'vitest';
import { descreverOrigem, origemCurta } from '../lib/origem.js';

describe('origem rastreavel', () => {
  it('descreve rodada, experimento, runs, coluna e n', () => {
    const t = descreverOrigem({ rodada: 'r1', experiment_id: 7, run_ids: [3, 9], tabela: 'measurement', metrica: 'logical_reads', n: 30, fonte: 'tcclab.analise' });
    expect(t).toBe('rodada r1 · experimento 7 · runs 3, 9 · measurement.logical_reads · n=30 · tcclab.analise');
  });
  it('omite o que nao existe e nunca inventa', () => {
    expect(descreverOrigem({ rodada: 'r1' })).toBe('rodada r1');
    expect(descreverOrigem(null)).toBe('');
    expect(origemCurta({})).toBe('');
  });
  it('singular e plural', () => {
    expect(origemCurta({ experiment_id: 1, run_ids: [5] })).toBe('exp 1 · run 5');
    expect(origemCurta({ linhas: [4, 5, 6, 7] })).toBe('escrita 4–7');
  });
});
