import { createContext, useContext } from 'react';

/** Estado compartilhado: rodada atual, lista de rodadas, estado da API e catalogo (carregados uma vez no App). */
export const Contexto = createContext({
  rodada: null,
  setRodada: () => {},
  rodadas: { dados: null, erro: null, carregando: true },
  estado: { dados: null, erro: null, carregando: true },
  catalogo: { dados: null, erro: null, carregando: true },
  recarregar: () => {},
});

export const useContexto = () => useContext(Contexto);

export function rodadaAtual(ctx) {
  return ctx.rodadas.dados?.rodadas?.find((r) => r.rodada === ctx.rodada) ?? null;
}
