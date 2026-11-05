import { useMemo } from 'react';
import { Num } from '../../componentes/Num.jsx';
import { Aviso, Badge, Carregando, EstadoVazio } from '../../componentes/EstadoVazio.jsx';
import { fmtData, fmtInt, fmtPct, fmtNum, rotuloSeletividade, VAZIO } from '../../lib/formatar.js';
import { mensagemDeErro } from '../../lib/api.js';
import { opcoesDePlano, origemPlano, usePlano } from './Diagnostico.jsx';

/** Objetos (tabela/indice) acessados no plano, com cardinalidade e fracao devolvida; agrupados por objeto. */
export function objetosDoPlano(plano) {
  const mapa = new Map();
  for (const o of plano?.operadores ?? []) {
    if (!o.objeto) continue;
    const m = mapa.get(o.objeto) ?? { objeto: o.objeto, tipo_indice: o.tipo_indice, operadores: [] };
    m.operadores.push(o);
    mapa.set(o.objeto, m);
  }
  return [...mapa.values()];
}

export default function Analise({ id, rodada, detalhe, validacao, catalogo, planoSel, setPlanoSel }) {
  const opcoes = useMemo(() => opcoesDePlano(detalhe, validacao.dados), [detalhe, validacao.dados]);
  const sel = opcoes.find((o) => o.id === planoSel) ?? opcoes[0] ?? null;
  const plano = usePlano(id, rodada, sel);
  const resumo = plano.dados?.resumo;
  const objetos = useMemo(() => objetosDoPlano(resumo), [resumo]);
  const org = origemPlano(sel, rodada, plano.dados);
  const real = resumo?.eh_plano_real;

  return (
    <>
      <div className="camada"><span className="numero" aria-hidden="true">2</span><h2>Análise: índices, estatísticas e seletividade</h2></div>

      <section className="cartao" aria-labelledby="a-decl">
        <header><h3 id="a-decl">O que o catálogo declara</h3><span className="sub">fonte: {detalhe.arquivo}</span></header>
        <div className="corpo">
          <dl className="kv">
            <dt>Tabelas</dt><dd className="mono">{detalhe.tabelas.join(', ')}</dd>
            <dt>Seletividade</dt><dd><Badge tipo="acento">{rotuloSeletividade(detalhe.seletividade)}</Badge> <span className="sub">{catalogo?.dados?.legenda_seletividade ? `(${catalogo.dados.legenda_seletividade})` : ''}</span></dd>
            <dt>Papel no experimento</dt><dd>{detalhe.papel}</dd>
          </dl>
          <p className="nota">A seletividade acima é a classe declarada pelo autor; a observada no plano está na tabela seguinte.</p>
        </div>
      </section>

      <section className="cartao" aria-labelledby="a-obj">
        <header>
          <h3 id="a-obj">Objetos acessados no plano</h3>
          {opcoes.length ? (
            <label className="campo"><span className="so-leitor">Fonte do plano</span>
              <select name="selecao" value={sel?.id ?? ''} onChange={(e) => setPlanoSel(e.target.value)} aria-label="Fonte do plano">
                {opcoes.map((o) => <option key={o.id} value={o.id}>{o.rotulo}</option>)}
              </select>
            </label>
          ) : null}
        </header>
        <div className="corpo sem-padding">
          {!opcoes.length ? <div className="corpo"><EstadoVazio titulo="Sem plano para analisar" motivo="Sem planos salvos nem gravados em medição para esta consulta." /></div>
            : plano.carregando ? <div className="corpo"><Carregando /></div>
              : plano.erro ? <div className="corpo"><Aviso tipo="erro" titulo="Plano indisponível">{mensagemDeErro(plano.erro)}</Aviso></div>
                : !resumo?.valido ? <div className="corpo"><EstadoVazio titulo="Plano ilegível" motivo={resumo?.motivo ?? plano.dados?.motivo_sem_xml} /></div>
                  : (
                    <div className="rolagem">
                      <table className="tabela">
                        <caption>Cardinalidade da tabela e fração das linhas que cada operador devolve ({real ? 'linhas reais' : 'linhas estimadas'} ÷ cardinalidade da tabela), lidas do XML.</caption>
                        <thead><tr><th scope="col">Objeto</th><th scope="col">Tipo</th><th scope="col">Operador</th><th scope="col" className="n">Cardinalidade da tabela</th><th scope="col" className="n">Linhas lidas</th><th scope="col" className="n">Linhas devolvidas</th><th scope="col" className="n">Fração da tabela</th></tr></thead>
                        <tbody>
                          {objetos.flatMap((ob) => ob.operadores.map((o) => (
                            <tr key={`${ob.objeto}-${o.node_id}`}>
                              <th scope="row" className="mono">{ob.objeto}</th>
                              <td>{ob.tipo_indice ?? VAZIO}</td>
                              <td>{o.operador_fisico} <span className="sub">nó {o.node_id}</span></td>
                              <td className="n"><Num valor={o.cardinalidade_tabela} origem={{ ...org, nota: `nó ${o.node_id}: TableCardinality` }} rotulo="cardinalidade da tabela" /></td>
                              <td className="n"><Num valor={o.linhas_lidas_reais} origem={{ ...org, nota: `nó ${o.node_id}: ActualRowsRead` }} rotulo="linhas lidas" /></td>
                              <td className="n"><Num valor={real ? o.linhas_reais : o.linhas_estimadas} origem={{ ...org, nota: `nó ${o.node_id}: ${real ? 'ActualRows' : 'EstimateRows'}` }} rotulo="linhas devolvidas" /></td>
                              <td className="n"><Num valor={(real ? o.fracao_real : o.fracao_estimada) == null ? null : (real ? o.fracao_real : o.fracao_estimada) * 100} formato={(x) => fmtPct(x, x < 0.1 ? 3 : 2)} origem={{ ...org, nota: `nó ${o.node_id}: ${real ? 'ActualRows' : 'EstimateRows'} ÷ TableCardinality` }} rotulo="fração da tabela" /></td>
                            </tr>
                          )))}
                          {!objetos.length ? <tr><td colSpan={7} className="sub">O plano não referencia tabela ou índice.</td></tr> : null}
                        </tbody>
                      </table>
                    </div>
                  )}
        </div>
      </section>

      {resumo?.valido ? (
        <section className="cartao" aria-labelledby="a-est">
          <header><h3 id="a-est">Estatísticas lidas pelo otimizador</h3><span className="sub">OptimizerStatsUsage do plano</span></header>
          <div className="corpo sem-padding">
            {resumo.estatisticas_usadas?.length ? (
              <div className="rolagem">
                <table className="tabela">
                  <thead><tr><th scope="col">Tabela</th><th scope="col">Estatística</th><th scope="col">Última atualização</th><th scope="col" className="n">Modificações desde então</th><th scope="col" className="n">Amostragem</th></tr></thead>
                  <tbody>
                    {resumo.estatisticas_usadas.map((e, i) => (
                      <tr key={i}>
                        <th scope="row" className="mono">{e.tabela ?? VAZIO}</th><td className="mono">{e.estatistica ?? VAZIO}</td><td>{fmtData(e.ultima_atualizacao)}</td>
                        <td className="n"><Num valor={e.modificacoes} origem={{ ...org, nota: `StatisticsInfo ${e.estatistica}: ModificationCount` }} rotulo="modificações" /></td>
                        <td className="n"><Num valor={e.amostragem_pct} formato={(x) => fmtPct(x, 1)} origem={{ ...org, nota: `StatisticsInfo ${e.estatistica}: SamplingPercent` }} rotulo="amostragem" /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <div className="corpo"><EstadoVazio titulo="O plano não registra estatísticas usadas" motivo="Sem elemento OptimizerStatsUsage neste XML." /></div>}
          </div>
        </section>
      ) : null}

      <section className="cartao" aria-labelledby="a-causa">
        <header><h3 id="a-causa">Causa classificada</h3></header>
        <div className="corpo">
          <EstadoVazio titulo="Classificador de causa ainda não disponível" motivo="O núcleo não tem módulo de análise de causa (índice ausente, não utilizável, ordem de colunas, cobertura, estatística). O inventário completo de índices (sys.indexes) também não é coletado: só os índices que aparecem nos planos são mostrados." />
        </div>
      </section>
    </>
  );
}
