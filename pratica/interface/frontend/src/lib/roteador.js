// Roteador por hash (#/painel, #/catalogo, #/consulta/F1?rodada=r1&aba=validacao). Sem dependencia externa.
export const TELAS = ['painel', 'catalogo', 'consulta', 'historico', 'ambiente'];

export function analisarHash(hash) {
  const bruto = (hash || '').replace(/^#\/?/, '');
  const [caminho, consulta = ''] = bruto.split('?');
  const partes = caminho.split('/').filter(Boolean).map(decodeURIComponent);
  const params = Object.fromEntries(new URLSearchParams(consulta));
  const tela = TELAS.includes(partes[0]) ? partes[0] : 'painel';
  return { tela, id: partes[1] ?? null, params };
}

export function montarHash(tela, id = null, params = {}) {
  const caminho = ['', tela, id ? encodeURIComponent(id) : null].filter((x) => x !== null).join('/');
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== ''));
  const s = q.toString();
  return `#${caminho}${s ? `?${s}` : ''}`;
}
