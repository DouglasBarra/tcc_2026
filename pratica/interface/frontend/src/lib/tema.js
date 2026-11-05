// Tema claro/escuro/sistema. O storage pode falhar (janela privada); a pagina funciona sem ele.
const CHAVE = 'tcclab.tema';

export function lerPreferencia() {
  try {
    const v = localStorage.getItem(CHAVE);
    return v === 'claro' || v === 'escuro' ? v : 'sistema';
  } catch {
    return 'sistema';
  }
}

export function aplicarTema(pref, raiz = document.documentElement) {
  if (pref === 'claro' || pref === 'escuro') raiz.setAttribute('data-tema', pref);
  else raiz.removeAttribute('data-tema');
}

export function salvarPreferencia(pref) {
  try {
    if (pref === 'sistema') localStorage.removeItem(CHAVE);
    else localStorage.setItem(CHAVE, pref);
  } catch {
    /* sem storage: a escolha vale so nesta sessao */
  }
}

export function proximaPreferencia(atual) {
  return atual === 'sistema' ? 'claro' : atual === 'claro' ? 'escuro' : 'sistema';
}

export function guardar(chave, valor) {
  try {
    localStorage.setItem(`tcclab.${chave}`, JSON.stringify(valor));
  } catch {
    /* ignora */
  }
}

export function recuperar(chave, padrao) {
  try {
    const v = localStorage.getItem(`tcclab.${chave}`);
    return v === null ? padrao : JSON.parse(v);
  } catch {
    return padrao;
  }
}
