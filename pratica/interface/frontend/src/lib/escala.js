/**
 * Fator --escala-fonte (tokens.css) para o que o CSS nao alcanca: os SVG tem fonte e margens em unidades do viewBox.
 * Dividir a largura do viewBox por ele amplia rotulos, margens e barras juntos. Sem CSS carregado (testes), vale 1.
 */
export function escalaFonte() {
  const v = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--escala-fonte'));
  return v > 0 ? v : 1;
}
