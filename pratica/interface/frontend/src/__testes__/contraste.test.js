// Acessibilidade: contraste minimo 4,5:1 (WCAG AA, texto normal) para os pares de tokens usados como texto.
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(join(process.cwd(), 'src', 'estilos', 'tokens.css'), 'utf8'); // vitest roda na pasta frontend/

function bloco(seletor) {
  const i = css.indexOf(seletor);
  const ini = css.indexOf('{', i);
  let nivel = 0;
  for (let k = ini; k < css.length; k += 1) {
    if (css[k] === '{') nivel += 1;
    if (css[k] === '}') { nivel -= 1; if (nivel === 0) return css.slice(ini + 1, k); }
  }
  return '';
}
const vars = (txt) => Object.fromEntries([...txt.matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map((m) => [m[1], m[2]]));

const escuro = vars(bloco(':root {'));
const claro = { ...escuro, ...vars(bloco(":root[data-tema='claro']")) };

function lum(hex) {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
export function contraste(a, b) {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

const PARES = [
  ['texto', 'bg'], ['texto', 'bg-2'], ['texto', 'bg-3'],
  ['texto-2', 'bg'], ['texto-2', 'bg-2'], ['texto-2', 'bg-3'],
  ['acento', 'bg'], ['acento', 'bg-2'], ['sobre-acento', 'acento'],
  ['texto', 'acento-fundo'], ['texto', 'ok-fundo'], ['texto', 'alerta-fundo'], ['texto', 'erro-fundo'], ['texto', 'info-fundo'],
  ['sql-keyword', 'bg-2'], ['sql-string', 'bg-2'], ['sql-number', 'bg-2'], ['sql-comment', 'bg-2'], ['sql-ident', 'bg-2'], ['sql-type', 'bg-2'],
  ['serie-1', 'bg-2'], ['serie-2', 'bg-2'], ['serie-3', 'bg-2'], ['serie-4', 'bg-2'], ['serie-5', 'bg-2'], ['serie-6', 'bg-2'],
];

describe.each([['escuro', escuro], ['claro', claro]])('contraste do tema %s', (_nome, t) => {
  it('os tokens existem', () => {
    for (const [a, b] of PARES) { expect(t[a], a).toBeTruthy(); expect(t[b], b).toBeTruthy(); }
  });
  it.each(PARES)('%s sobre %s >= 4,5:1', (a, b) => {
    expect(contraste(t[a], t[b])).toBeGreaterThanOrEqual(4.5);
  });
  it('foco visivel tem contraste >= 3:1 com o fundo', () => {
    expect(contraste(t.foco, t.bg)).toBeGreaterThanOrEqual(3);
    expect(contraste(t.foco, t['bg-2'])).toBeGreaterThanOrEqual(3);
  });
});
