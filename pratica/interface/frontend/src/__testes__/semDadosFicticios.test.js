// Criterio M5: "busca por Math.random e por dados embutidos retorna zero" no codigo da interface (fora dos testes).
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const RAIZ = join(process.cwd(), 'src'); // vitest roda na pasta frontend/

function arquivos(dir) {
  return readdirSync(dir).flatMap((nome) => {
    const caminho = join(dir, nome);
    if (statSync(caminho).isDirectory()) return nome === '__testes__' ? [] : arquivos(caminho);
    return [caminho];
  });
}

const FONTES = arquivos(RAIZ);

describe('interface sem dados ficticios', () => {
  it('ha codigo-fonte para varrer', () => {
    expect(FONTES.filter((f) => /\.(jsx?|css)$/.test(f)).length).toBeGreaterThan(20);
  });

  it.each([
    ['Math.random', /Math\.random/],
    ['crypto.getRandomValues', /getRandomValues/],
    ['faker', /faker/i],
    ['lorem ipsum', /lorem ipsum/i],
    ['mock/fixture/dummy/sample de dados', /\b(mockData|mockdata|fixtureData|dummyData|sampleData|fakeData)\b/i],
  ])('nao contem %s', (_nome, re) => {
    const achados = FONTES.filter((f) => re.test(readFileSync(f, 'utf8'))).map((f) => relative(RAIZ, f));
    expect(achados).toEqual([]);
  });

  it('nao embute arquivos de dados (.json, .csv, .sqlite)', () => {
    expect(FONTES.filter((f) => /\.(json|csv|sqlite|db)$/.test(f))).toEqual([]);
  });

  it('nao embute series numericas literais (heuristica: lista de 5 ou mais numeros)', () => {
    const re = /\[\s*-?\d+(\.\d+)?(\s*,\s*-?\d+(\.\d+)?){4,}\s*\]/;
    const achados = FONTES.filter((f) => /\.jsx?$/.test(f) && re.test(readFileSync(f, 'utf8'))).map((f) => relative(RAIZ, f));
    expect(achados).toEqual([]);
  });
});
