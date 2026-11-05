import { useCallback, useEffect, useRef, useState } from 'react';
import { obter } from '../lib/api.js';

/**
 * Carrega `url` (null = nao carrega). Devolve { dados, erro, carregando, recarregar }.
 * `intervaloMs`: repete a leitura enquanto `continuar(dados)` for verdadeiro (ex.: analise em calculo).
 */
export function useApi(url, { intervaloMs = 0, continuar = () => false } = {}) {
  const [estado, setEstado] = useState({ dados: null, erro: null, carregando: Boolean(url) });
  const [versao, setVersao] = useState(0);
  const continuarRef = useRef(continuar);
  continuarRef.current = continuar;

  useEffect(() => {
    if (!url) {
      setEstado({ dados: null, erro: null, carregando: false });
      return undefined;
    }
    const ctl = new AbortController();
    let timer = null;
    let vivo = true;
    setEstado((e) => ({ ...e, carregando: true }));
    const carregar = async () => {
      try {
        const dados = await obter(url, { signal: ctl.signal });
        if (!vivo) return;
        setEstado({ dados, erro: null, carregando: false });
        if (intervaloMs > 0 && continuarRef.current(dados)) timer = setTimeout(carregar, intervaloMs);
      } catch (erro) {
        if (!vivo || erro?.name === 'AbortError') return;
        setEstado({ dados: null, erro, carregando: false });
      }
    };
    carregar();
    return () => {
      vivo = false;
      ctl.abort();
      if (timer) clearTimeout(timer);
    };
  }, [url, versao, intervaloMs]);

  const recarregar = useCallback(() => setVersao((v) => v + 1), []);
  return { ...estado, recarregar };
}
