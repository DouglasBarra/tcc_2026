import { useCallback, useEffect, useMemo, useState } from 'react';
import { Contexto } from './contexto.jsx';
import { useApi } from './hooks/useApi.js';
import { analisarHash, montarHash } from './lib/roteador.js';
import { aplicarTema, guardar, lerPreferencia, proximaPreferencia, recuperar, salvarPreferencia } from './lib/tema.js';
import { fmtHa, rotuloEstadoRodada, rotuloTipoRodada } from './lib/formatar.js';
import { mensagemDeErro } from './lib/api.js';
import { IconeAmbiente, IconeAuto, IconeCatalogo, IconeConsulta, IconeHistorico, IconeLua, IconePainel, IconeSol } from './componentes/Icones.jsx';
import Painel from './telas/Painel.jsx';
import Catalogo from './telas/Catalogo.jsx';
import Consulta from './telas/Consulta.jsx';
import Historico from './telas/Historico.jsx';
import Ambiente from './telas/Ambiente.jsx';

const NAV = [
  ['painel', 'Painel', IconePainel],
  ['catalogo', 'Catálogo', IconeCatalogo],
  ['consulta', 'Consulta', IconeConsulta],
  ['historico', 'Histórico', IconeHistorico],
  ['ambiente', 'Ambiente', IconeAmbiente],
];
const TITULOS = { painel: 'Visão geral do experimento', catalogo: 'Catálogo de consultas', consulta: 'Consulta', historico: 'Histórico de rodadas', ambiente: 'Ambiente' };

function useHash() {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const f = () => setHash(window.location.hash);
    window.addEventListener('hashchange', f);
    return () => window.removeEventListener('hashchange', f);
  }, []);
  return hash;
}

export default function App() {
  const hash = useHash();
  const rota = useMemo(() => analisarHash(hash), [hash]);
  const [pref, setPref] = useState(lerPreferencia);
  useEffect(() => {
    aplicarTema(pref);
  }, [pref]);

  const estado = useApi('/api/estado', { intervaloMs: 15000, continuar: () => true });
  const rodadas = useApi('/api/rodadas', { intervaloMs: 15000, continuar: (d) => d.rodadas.some((r) => r.estado === 'em_andamento') });
  const catalogo = useApi('/api/catalogo');

  const [rodadaEscolhida, setRodadaEscolhida] = useState(() => recuperar('rodada', null));
  const lista = rodadas.dados?.rodadas ?? [];
  const rodada = useMemo(() => {
    const doHash = rota.params.rodada;
    if (doHash && lista.some((r) => r.rodada === doHash)) return doHash;
    if (rodadaEscolhida && lista.some((r) => r.rodada === rodadaEscolhida)) return rodadaEscolhida;
    return (lista.find((r) => r.rodada === 'r2-compat160' && r.n_medicoes > 0) ?? lista.find((r) => r.n_medicoes > 0 && r.tipo === 'coleta') ?? lista.find((r) => r.n_medicoes > 0) ?? lista[0])?.rodada ?? null;
  }, [rota.params.rodada, rodadaEscolhida, lista]);

  const setRodada = useCallback((r) => {
    setRodadaEscolhida(r);
    guardar('rodada', r);
    const { tela, id, params } = analisarHash(window.location.hash);
    if (params.rodada) window.location.hash = montarHash(tela, id, { ...params, rodada: r, exp: undefined });
  }, []);

  const recarregar = useCallback(() => {
    rodadas.recarregar();
    estado.recarregar();
  }, [rodadas, estado]);
  const ctx = useMemo(() => ({ rodada, setRodada, rodadas, estado, catalogo, recarregar }), [rodada, setRodada, rodadas, estado, catalogo, recarregar]);

  const emAndamento = lista.filter((r) => r.estado === 'em_andamento');
  const aoVivo = estado.dados?.ao_vivo;
  const trava = aoVivo?.trava;

  const icone = pref === 'claro' ? <IconeSol /> : pref === 'escuro' ? <IconeLua /> : <IconeAuto />;
  const nomePref = { sistema: 'automático', claro: 'claro', escuro: 'escuro' }[pref];

  const Tela = { painel: Painel, catalogo: Catalogo, consulta: Consulta, historico: Historico, ambiente: Ambiente }[rota.tela];

  return (
    <Contexto.Provider value={ctx}>
      <div className="app">
        <nav className="trilho" aria-label="Telas">
          <a className="marca" href="#/painel" aria-label="TCC Lab, ir para o painel"><span className="logo">TCC<small>Lab</small></span></a>
          {NAV.map(([id, rotulo, Icone]) => (
            <a key={id} href={montarHash(id, id === 'consulta' ? rota.id : null, id === 'consulta' || id === 'historico' ? { rodada } : {})} aria-current={rota.tela === id ? 'page' : undefined}>
              <Icone /> <span>{rotulo}</span>
            </a>
          ))}
          <div className="espaco" />
          <button type="button" className="tema" onClick={() => { const n = proximaPreferencia(pref); setPref(n); salvarPreferencia(n); }} aria-label={`Tema: ${nomePref}. Alternar tema`}>
            {icone} <span>{nomePref}</span>
          </button>
        </nav>
        <header className="topo">
          <div className="migalha">
            <span className="app-nome">TCC Lab</span><span className="sep" aria-hidden="true">/</span>
            <span className="tela-nome">{TITULOS[rota.tela]}{rota.tela === 'consulta' && rota.id ? ` ${rota.id}` : ''}</span>
          </div>
          <div className="espaco" />
          <div className="ferramentas">
            <span className="badge" title={aoVivo?.habilitado ? 'Caminhos ao vivo habilitados' : aoVivo?.motivo_desabilitado ?? 'Caminhos ao vivo desligados'}>
              <span className={`ponto-vivo ${aoVivo?.habilitado ? 'ligado' : ''}`} aria-hidden="true" /> {aoVivo?.habilitado ? 'ao vivo: ligado' : 'ao vivo: desligado'}
            </span>
            <div className="seletor-rodada">
              <label htmlFor="sel-rodada">Rodada</label>
              <select name="selecao" id="sel-rodada" value={rodada ?? ''} onChange={(e) => setRodada(e.target.value)} disabled={!lista.length}>
                {!lista.length ? <option value="">nenhuma</option> : null}
                {lista.map((r) => (
                  <option key={r.rodada} value={r.rodada}>
                    {r.rodada} ({rotuloTipoRodada(r.tipo)}, {rotuloEstadoRodada(r.estado)})
                  </option>
                ))}
              </select>
            </div>
          </div>
        </header>
        <div className="faixas">
          {estado.erro ? <div className="faixa erro" role="alert">{mensagemDeErro(estado.erro)}</div> : null}
          {emAndamento.map((r) => (
            <div className="faixa alerta" key={r.rodada} role="status">
              <strong>Coleta em andamento: {r.rodada}</strong>
              <span>atualizada {fmtHa(r.modificada_ha_s)}. Os números dessa rodada são parciais e o modo ao vivo fica bloqueado (HTTP 409) para não perturbar as medições.</span>
            </div>
          ))}
          {trava?.ocupada ? (
            <div className="faixa alerta" role="status">
              <strong>Trava global ocupada</strong>
              <span>{trava.dono} (desde {trava.desde_utc} UTC). Só um experimento por vez.</span>
            </div>
          ) : null}
        </div>
        <main id="conteudo" tabIndex={-1}>
          <Tela rota={rota} />
        </main>
      </div>
    </Contexto.Provider>
  );
}
