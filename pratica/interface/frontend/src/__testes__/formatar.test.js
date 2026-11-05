import { describe, expect, it } from 'vitest';
import { fmtBytes, fmtData, fmtHa, fmtHash, fmtIC, fmtInt, fmtLista, fmtMs, fmtNum, fmtP, fmtPct, fmtRazao, fmtSig, rotuloMetrica, rotuloRegime, VAZIO } from '../lib/formatar.js';

const NB = ' ';

describe('formatadores pt-BR', () => {
  it('usa ponto de milhar e virgula decimal', () => {
    expect(fmtNum(1234567.891, 2)).toBe('1.234.567,89');
    expect(fmtInt(800856)).toBe('800.856');
    expect(fmtNum(0.5, 1)).toBe('0,5');
  });

  it('sem dado vira travessao, nunca zero', () => {
    for (const v of [null, undefined, NaN, Infinity, '12']) {
      expect(fmtNum(v, 2)).toBe(VAZIO);
      expect(fmtInt(v)).toBe(VAZIO);
      expect(fmtPct(v)).toBe(VAZIO);
      expect(fmtRazao(v)).toBe(VAZIO);
      expect(fmtMs(v)).toBe(VAZIO);
    }
    expect(fmtNum(0, 0)).toBe('0'); // zero medido e um dado, diferente de ausente
  });

  it('nao produz -0', () => {
    expect(fmtNum(-0.0001, 2)).toBe('0,00');
    expect(fmtNum(-0, 0)).toBe('0');
  });

  it('inteiro nao esconde fracao', () => {
    expect(fmtInt(7807.5)).toBe('7.807,50');
  });

  it('razao com 3 algarismos significativos', () => {
    expect(fmtRazao(0.05)).toBe('0,050');
    expect(fmtRazao(0.0000512)).toBe('0,0000512');
    expect(fmtSig(1234.5)).toBe('1.235');
    expect(fmtRazao(1)).toBe('1,0');
  });

  it('percentual com sinal tipografico e espaco nao separavel', () => {
    expect(fmtPct(-94.7)).toBe(`−94,7${NB}%`);
    expect(fmtPct(20, 0)).toBe(`20${NB}%`);
    expect(fmtPct(3.25, 1, true)).toBe(`+3,3${NB}%`);
    expect(fmtPct(0)).toBe(`0,0${NB}%`);
  });

  it('percentual nao arredonda 99,995 para 100 nem 0,003 para zero', () => {
    expect(fmtPct(-99.995)).toBe(`−99,995${NB}%`);
    expect(fmtPct(100)).toBe(`100,0${NB}%`);
    expect(fmtPct(0.003)).toBe(`0,003${NB}%`);
  });

  it('intervalo de confianca com ponto e virgula', () => {
    expect(fmtIC(0.05, 0.062)).toBe('[0,050; 0,062]');
    expect(fmtIC(null, 0.5)).toBe(VAZIO);
  });

  it('unidades e bytes', () => {
    expect(fmtMs(13)).toBe(`13${NB}ms`);
    expect(fmtBytes(1536)).toBe(`1,5${NB}KiB`);
    expect(fmtBytes(512)).toBe(`512${NB}B`);
  });

  it('valor p', () => {
    expect(fmtP(0.0004)).toBe(`<${NB}0,001`);
    expect(fmtP(0.0312)).toBe('0,031');
  });

  it('datas UTC como gravadas, sem converter fuso', () => {
    expect(fmtData('2026-10-25 17:52:11.123456')).toBe('25/10/2026 17:52:11 UTC');
    expect(fmtData(null)).toBe(VAZIO);
    expect(fmtData('lixo')).toBe(VAZIO);
  });

  it('tempo decorrido', () => {
    expect(fmtHa(12)).toBe('há 12 s');
    expect(fmtHa(300)).toBe('há 5 min');
    expect(fmtHa(7200)).toBe('há 2 h');
    expect(fmtHa(null)).toBe(VAZIO);
  });

  it('lista de ids sem separador de milhar e hash curto', () => {
    expect(fmtLista([1, 1500, 2])).toBe('1, 1500, 2');
    expect(fmtLista([])).toBe(VAZIO);
    expect(fmtHash('0x25255CFFBD7EC1FD')).toBe('0x25255CFF…');
  });

  it('rotulos em portugues', () => {
    expect(rotuloMetrica('logical_reads')).toBe('Leituras lógicas');
    expect(rotuloMetrica('cpu_ms')).toBe('CPU (ms)');
    expect(rotuloMetrica('elapsed_ms')).toBe('Tempo de execução (ms)');
    expect(rotuloRegime('quente')).toBe('Quente');
  });
});
