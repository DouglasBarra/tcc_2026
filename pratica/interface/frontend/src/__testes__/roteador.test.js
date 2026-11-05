import { describe, expect, it } from 'vitest';
import { analisarHash, montarHash } from '../lib/roteador.js';

describe('roteador por hash', () => {
  it('le tela, id e parametros', () => {
    expect(analisarHash('#/consulta/F1?rodada=r1&aba=validacao')).toEqual({ tela: 'consulta', id: 'F1', params: { rodada: 'r1', aba: 'validacao' } });
  });
  it('tela desconhecida cai no painel', () => {
    expect(analisarHash('#/qualquer').tela).toBe('painel');
    expect(analisarHash('').tela).toBe('painel');
  });
  it('monta e desmonta sem perda, ignorando parametros vazios', () => {
    const h = montarHash('consulta', 'S1', { rodada: 'r1-compat160', aba: 'analise', exp: undefined, vazio: '' });
    expect(h).toBe('#/consulta/S1?rodada=r1-compat160&aba=analise');
    expect(analisarHash(h).params).toEqual({ rodada: 'r1-compat160', aba: 'analise' });
  });
});
