import { useEffect, useState } from 'react';
import { useContexto, rodadaAtual } from '../contexto.jsx';
import { useApi } from '../hooks/useApi.js';
import { montarUrl, mensagemDeErro } from '../lib/api.js';
import { montarHash } from '../lib/roteador.js';
import { Aviso, Badge, Carregando, EstadoVazio } from '../componentes/EstadoVazio.jsx';
import { Num } from '../componentes/Num.jsx';
import { fmtData, fmtHa, fmtHash, fmtInt, fmtNum, fmtP, fmtPct, fmtRazao, rotuloEstadoRodada, rotuloMetrica, rotuloRegime, rotuloTipoRodada } from '../lib/formatar.js';

const TIPO_ESTADO = { em_andamento: 'alerta', interrompida: 'erro', concluida: 'ok', com_erro: 'erro', vazia: '' };

function ListaRodadas({ rodadas, rodada, onEscolher }) {
  if (!rodadas.length) {
    return <EstadoVazio titulo="Nenhuma rodada encontrada" motivo="Não há tcclab.sqlite em pratica/experimento/resultados/<rodada>/. A coleta (experimento/coletar.py) cria esses arquivos." />;
  }
  return (
    <ul className="lista-sel" aria-label="Rodadas">
      {rodadas.map((r) => (
        <li key={r.rodada}>
          <button type="button" aria-current={r.rodada === rodada ? 'true' : undefined} onClick={() => onEscolher(r.rodada)}>
            <span><strong className="mono">{r.rodada}</strong> <Badge>{rotuloTipoRodada(r.tipo)}</Badge> <Badge tipo={TIPO_ESTADO[r.estado] ?? ''}>{rotuloEstadoRodada(r.estado)}</Badge></span>
            {r.compativel ? (
              <span className="sub">
                <Num valor={r.n_experimentos} origem={{ rodada: r.rodada, tabela: 'experiment', nota: 'experimentos gravados' }} rotulo="experimentos" focavel={false} /> exp ·{' '}
                <Num valor={r.n_runs} origem={{ rodada: r.rodada, tabela: 'run', nota: 'runs gravados' }} rotulo="runs" focavel={false} /> runs ·{' '}
                <Num valor={r.n_medicoes} origem={{ rodada: r.rodada, tabela: 'measurement', nota: 'repetições medidas (fase measured)' }} rotulo="medições" focavel={false} /> medições · {fmtHa(r.modificada_ha_s)}
              </span>
            ) : <span className="sub">incompatível: {r.motivo_incompativel}</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}

const ROTULO_ANALISE = {
  principal: 'principal: consultas de papel principal',
  sem_atipicos: 'principal sem os valores atípicos (sensibilidade)',
  com_controles: 'com os controles (sensibilidade)',
  controles: 'só os controles',
  secundarias: 'braços secundários (reescrita, índice, nativo…)',
};

function Agregado({ rodada, andamento }) {
  const [pedir, setPedir] = useState(false);
  useEffect(() => setPedir(false), [rodada]);
  const a = useApi(rodada ? montarUrl(`/api/rodadas/${rodada}/analise`, { calcular: pedir ? 'true' : undefined }) : null, { intervaloMs: 1500, continuar: (d) => d?.estado === 'calculando' });
  const r = a.dados;
  return (
    <section className="cartao" aria-labelledby="h-agg">
      <header>
        <h2 id="h-agg">Resultados entre consultas</h2>
        <span className="sub">tcclab.analise: uma razão por consulta, Wilcoxon pareado, Holm</span>
      </header>
      <div className="corpo sem-padding">
        {a.carregando && !r ? <div className="corpo"><Carregando /></div>
          : a.erro ? <div className="corpo"><Aviso tipo="erro" titulo="Falha">{mensagemDeErro(a.erro)}</Aviso></div>
            : r?.estado === 'nao_calculada' ? (
              <div className="corpo">
                <EstadoVazio titulo="Análise entre consultas ainda não calculada" motivo={`Bootstrap e testes usam CPU (${r.n_experimentos} experimento(s) concluído(s)); por isso só rodam quando você pede.${andamento ? ' Esta rodada está em andamento: o cálculo pode perturbar as medições de tempo da coleta e usa dados parciais.' : ''}`}>
                  <div><button type="button" className="btn primario" onClick={() => setPedir(true)}>Calcular análise entre consultas</button></div>
                </EstadoVazio>
              </div>
            )
            : r?.estado === 'calculando' ? <div className="corpo"><Carregando texto={`Calculando bootstrap e testes (tcclab.analise)… ${r.desde_s ?? 0} s`} /></div>
              : r?.estado !== 'pronto' ? <div className="corpo"><EstadoVazio titulo="Análise entre consultas indisponível" motivo={r?.motivo ?? r?.estado} /></div>
                : <TabelaAgregados r={r} rodada={rodada} />}
      </div>
    </section>
  );
}

function TabelaAgregados({ r, rodada }) {
  const res = r.resultado;
  const conjuntos = [...new Set(res.agregados.map((x) => x.analise))];
  const [conjunto, setConjunto] = useState('principal');
  const [estrato, setEstrato] = useState('todas');
  const ativo = conjuntos.includes(conjunto) ? conjunto : conjuntos[0];
  const doConjunto = res.agregados.filter((x) => x.analise === ativo);
  const estratos = [...new Set(doConjunto.map((x) => x.estrato_tipo))];
  const linhas = doConjunto
    .filter((x) => x.estrato_tipo === (estratos.includes(estrato) ? estrato : 'todas'))
    .sort((a, b) => Number(b.regime === res.criterios.regime_principal) - Number(a.regime === res.criterios.regime_principal)); // regime do criterio primeiro
  if (!res.agregados.length) return <div className="corpo"><EstadoVazio titulo="Sem comparações agregadas" motivo="Nenhum experimento concluído tem tratamento comparável com a linha de base." /></div>;
  return (
    <>
      <div className="corpo">
        <div className="campo-linha">
          <label className="campo"><span>Conjunto de consultas</span>
            <select name="selecao" value={ativo} onChange={(e) => setConjunto(e.target.value)}>{conjuntos.map((c) => <option key={c} value={c}>{ROTULO_ANALISE[c] ?? c}</option>)}</select>
          </label>
          <label className="campo"><span>Estrato</span>
            <select name="selecao" value={estratos.includes(estrato) ? estrato : 'todas'} onChange={(e) => setEstrato(e.target.value)}>{estratos.map((c) => <option key={c} value={c}>{{ todas: 'todas as consultas', antipadrao: 'por antipadrão', seletividade: 'por seletividade' }[c] ?? c}</option>)}</select>
          </label>
        </div>
        {res.descartados?.length ? <Aviso tipo="alerta" titulo="Tentativas excluídas por deriva"><ul>{res.descartados.map((d) => <li key={d.experiment_id}>{d.query_id}: experimento {d.experiment_id} (tentativa {d.tentativa}, deriva {fmtPct((d.deriva_a1_a2 ?? NaN) * 100, 1)})</li>)}</ul></Aviso> : null}
        {res.avisos?.length ? <Aviso tipo="alerta" titulo="Avisos de tcclab.analise"><ul>{res.avisos.map((a) => <li key={a}>{a}</li>)}</ul></Aviso> : null}
      </div>
      <div className="rolagem">
        <table className="tabela">
          <caption>Wilcoxon pareado sobre o log da razão (uma razão por consulta), correção de Holm; α = {fmtNum(res.criterios.alfa, 2)}; IC {fmtPct(res.criterios.nivel_ic * 100, 0)} por bootstrap ({fmtInt(res.criterios.reamostragens)} reamostragens, semente {res.criterios.semente}). Experimentos analisados: {r.experimentos_analisados.join(', ') || 'n/d'}.</caption>
          <thead><tr><th scope="col">Regime</th><th scope="col">Tratamento</th><th scope="col">Estrato</th><th scope="col">Métrica</th><th scope="col" className="n">Consultas</th><th scope="col" className="n">Redução mediana</th><th scope="col" className="n">IC 95 %</th><th scope="col" className="n">p (Holm)</th><th scope="col">Observação</th></tr></thead>
          <tbody>
            {linhas.map((x, i) => {
              const o = { rodada, tabela: 'analise (agregados)', metrica: x.metrica, nota: `${x.regime} · ${x.grupo ?? x.tratamento_kind} · consultas ${x.consultas}`, fonte: 'tcclab.analise', n: x.n_consultas };
              return (
                <tr key={i} className={x.regime === res.criterios.regime_principal ? '' : 'secundaria'}>
                  <td>{rotuloRegime(x.regime)}{x.regime === res.criterios.regime_principal ? '' : <span className="sub"> (à parte)</span>}</td><td>{x.grupo ?? x.tratamento_kind}</td><td>{x.estrato}</td><th scope="row">{rotuloMetrica(x.metrica)}</th>
                  <td className="n"><Num valor={x.n_consultas} origem={o} rotulo="consultas" /></td>
                  <td className="n"><Num valor={x.reducao_mediana_pct} formato={(v) => fmtPct(v, 1)} origem={o} rotulo="redução mediana" /></td>
                  <td className="n">{x.reducao_ic_lo != null ? <>[<Num valor={x.reducao_ic_lo} formato={(v) => fmtPct(v, 1)} origem={o} rotulo="IC inferior" />; <Num valor={x.reducao_ic_hi} formato={(v) => fmtPct(v, 1)} origem={o} rotulo="IC superior" />]{x.ic_confiavel ? '' : ' *'}</> : 'n/d'}</td>
                  <td className="n"><Num valor={x.p_holm} formato={fmtP} origem={o} rotulo="p ajustado" /></td>
                  <td className="sub">{x.observacao}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="nota" style={{ padding: '0 12px 8px' }}>* IC pouco confiável: menos consultas que o mínimo (n_min_consultas = {res.criterios.n_min_consultas}). Pilotos não entram na análise do pré-registro.</p>
    </>
  );
}

function Runs({ rodada }) {
  const [f, setF] = useState({ query_id: '', variante: '', regime: '' });
  const r = useApi(rodada ? montarUrl(`/api/rodadas/${rodada}/runs`, { ...f, limite: 400 }) : null);
  const d = r.dados;
  const upd = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }));
  return (
    <section className="cartao" aria-labelledby="h-runs">
      <header><h2 id="h-runs">Execuções (runs)</h2>
        {d ? <span className="sub" role="status"><Num valor={d.retornados} origem={{ rodada, tabela: 'run', nota: 'runs exibidos' }} rotulo="runs exibidos" /> de <Num valor={d.total} origem={{ rodada, tabela: 'run', nota: 'runs que passam pelos filtros' }} rotulo="runs no filtro" /></span> : null}
      </header>
      <div className="corpo">
        <div className="campo-linha" role="search" aria-label="Filtros das execuções">
          <label className="campo"><span>Consulta</span><select name="selecao" value={f.query_id} onChange={upd('query_id')}><option value="">todas</option>{(d?.filtros.consultas ?? []).map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="campo"><span>Variante</span><select name="selecao" value={f.variante} onChange={upd('variante')}><option value="">todas</option>{(d?.filtros.variantes ?? []).map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="campo"><span>Regime</span><select name="selecao" value={f.regime} onChange={upd('regime')}><option value="">todos</option>{(d?.filtros.regimes ?? []).map((x) => <option key={x} value={x}>{rotuloRegime(x)}</option>)}</select></label>
        </div>
      </div>
      <div className="corpo sem-padding">
        {r.carregando && !d ? <div className="corpo"><Carregando /></div> : r.erro ? <div className="corpo"><Aviso tipo="erro" titulo="Falha ao ler runs">{mensagemDeErro(r.erro)}</Aviso></div>
          : !d?.runs.length ? <div className="corpo"><EstadoVazio titulo="Nenhuma execução" motivo="Nenhum run corresponde aos filtros nesta rodada." /></div>
            : (
              <div className="rolagem">
                <table className="tabela">
                  <thead><tr><th scope="col">Run</th><th scope="col">Exp.</th><th scope="col">Consulta</th><th scope="col">Variante</th><th scope="col">Regime</th><th scope="col">Bloco</th><th scope="col">Status</th><th scope="col" className="n">n</th><th scope="col" className="n">Leituras lógicas (mediana)</th><th scope="col" className="n">CPU ms (mediana)</th><th scope="col" className="n">Tempo ms (mediana)</th><th scope="col">QueryPlanHash</th><th scope="col">Início</th></tr></thead>
                  <tbody>
                    {d.runs.map((x) => {
                      const o = (m) => ({ rodada, experiment_id: x.experiment_id, run_ids: [x.run_id], tabela: 'measurement', metrica: m, n: x.n, fonte: 'tcclab.stats' });
                      return (
                        <tr key={x.run_id}>
                          <th scope="row" className="mono">{x.run_id}</th><td className="mono">{x.experiment_id}</td>
                          <td><a className="mono" href={montarHash('consulta', x.query_id, { rodada, aba: 'validacao', exp: x.experiment_id })}>{x.query_id}</a></td>
                          <td>{x.variante}</td><td>{rotuloRegime(x.regime)}</td><td className="mono">{x.bloco}</td>
                          <td><Badge tipo={x.status === 'concluido' ? 'ok' : 'alerta'}>{x.status}</Badge>{x.avisos.length ? <Badge tipo="alerta" titulo={x.avisos.join('; ')}>aviso</Badge> : null}</td>
                          <td className="n"><Num valor={x.n} origem={{ ...o(null), nota: 'repetições medidas', metrica: undefined }} rotulo="n" /></td>
                          <td className="n"><Num valor={x.medianas.logical_reads} origem={o('logical_reads')} rotulo="leituras lógicas" /></td>
                          <td className="n"><Num valor={x.medianas.cpu_ms} origem={o('cpu_ms')} rotulo="CPU" /></td>
                          <td className="n"><Num valor={x.medianas.elapsed_ms} origem={o('elapsed_ms')} rotulo="tempo" /></td>
                          <td className="mono" title={x.query_plan_hash ?? ''}>{fmtHash(x.query_plan_hash)}</td>
                          <td className="nowrap">{fmtData(x.started_at)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
      </div>
    </section>
  );
}

function Experimentos({ detalhe }) {
  const exps = detalhe.experimentos ?? [];
  const coleta = Object.fromEntries((detalhe.coleta_consultas ?? []).filter((c) => c.experiment_id != null).map((c) => [c.experiment_id, c]));
  const semExp = (detalhe.coleta_consultas ?? []).filter((c) => c.experiment_id == null);
  return (
    <section className="cartao" aria-labelledby="h-exp">
      <header><h2 id="h-exp">Experimentos</h2></header>
      <div className="corpo sem-padding">
        {!exps.length ? <div className="corpo"><EstadoVazio titulo="Nenhum experimento nesta rodada" /></div> : (
          <div className="rolagem">
            <table className="tabela">
              <thead><tr><th scope="col">Id</th><th scope="col">Nome</th><th scope="col">Status</th><th scope="col">Tentativa</th><th scope="col" className="n">Deriva A1→A2 (CPU)</th><th scope="col">Descartado</th><th scope="col">Início</th><th scope="col">Fim</th></tr></thead>
              <tbody>
                {exps.map((e) => {
                  const c = coleta[e.experiment_id];
                  return (
                    <tr key={e.experiment_id}>
                      <th scope="row" className="mono">{e.experiment_id}</th><td>{e.name}</td>
                      <td><Badge tipo={e.status === 'concluido' ? 'ok' : e.status === 'erro' ? 'erro' : 'alerta'}>{e.status}</Badge></td>
                      <td className="n">{c?.tentativa ?? 'n/d'}</td>
                      <td className="n"><Num valor={c?.deriva_a1_a2 == null ? null : c.deriva_a1_a2 * 100} formato={(v) => fmtPct(v, 1)} origem={{ rodada: detalhe.rodada, experiment_id: e.experiment_id, tabela: 'coleta_consulta', metrica: 'deriva_a1_a2' }} rotulo="deriva" /></td>
                      <td>{c ? (c.descartado_por_deriva ? <Badge tipo="alerta">sim, por deriva</Badge> : 'não') : 'n/d'}</td>
                      <td className="nowrap">{fmtData(e.started_at)}</td><td className="nowrap">{fmtData(e.finished_at)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {semExp.length ? <div className="corpo"><Aviso tipo="alerta" titulo="Consultas registradas sem experimento">{semExp.map((c) => `${c.query_id}: ${c.status}`).join('; ')}</Aviso></div> : null}
      </div>
    </section>
  );
}

function AoVivo() {
  const t = useApi('/api/ao-vivo/trabalhos', { intervaloMs: 5000, continuar: () => true });
  const lista = t.dados?.trabalhos ?? [];
  return (
    <section className="cartao" aria-labelledby="h-vivo">
      <header><h2 id="h-vivo">Validações ao vivo desta sessão</h2></header>
      <div className="corpo">
        {!lista.length ? <EstadoVazio titulo="Nenhuma validação ao vivo nesta sessão" motivo="Elas aparecem aqui (e como rodadas ‘ao vivo’) depois de executadas na aba Validação." /> : (
          <ul className="lista-sel">{lista.map((j) => <li key={j.id}><span className="mono">{j.id}</span> <Badge tipo={j.estado === 'concluido' ? 'ok' : j.estado === 'executando' ? 'info' : 'erro'}>{j.estado}</Badge> <span className="sub">banco {j.banco} · {fmtData(j.criado_utc)}{j.rodada ? ` · rodada ${j.rodada}` : ''}</span></li>)}</ul>
        )}
      </div>
    </section>
  );
}

export default function Historico() {
  const ctx = useContexto();
  const { rodadas, rodada, setRodada } = ctx;
  const atual = rodadaAtual(ctx);
  const detalhe = useApi(rodada ? `/api/rodadas/${rodada}` : null, { intervaloMs: 10000, continuar: () => atual?.estado === 'em_andamento' });
  const lista = rodadas.dados?.rodadas ?? [];
  const p = atual?.parametros_rodada;
  return (
    <div className="pagina">
      <header>
        <div><h1>Histórico de execuções</h1><p>Rodadas de coleta, experimentos e runs gravados em SQLite. Nada aqui é calculado fora de tcclab.</p></div>
      </header>
      {rodadas.erro ? <Aviso tipo="erro" titulo="Rodadas indisponíveis">{mensagemDeErro(rodadas.erro)}</Aviso> : null}
      <div className="grade-hist">
        <section className="cartao" aria-labelledby="h-rod">
          <header><h2 id="h-rod">Rodadas</h2></header>
          <div className="corpo">{rodadas.carregando && !rodadas.dados ? <Carregando /> : <ListaRodadas rodadas={lista} rodada={rodada} onEscolher={setRodada} />}</div>
        </section>
        <div style={{ display: 'grid', gap: 16, minWidth: 0, alignContent: 'start' }}>
          {!rodada ? null : detalhe.carregando && !detalhe.dados ? <Carregando /> : detalhe.erro ? <Aviso tipo="erro" titulo="Rodada indisponível">{mensagemDeErro(detalhe.erro)}</Aviso> : detalhe.dados && !detalhe.dados.compativel ? <Aviso tipo="erro" titulo="Rodada incompatível">{detalhe.dados.motivo_incompativel}</Aviso> : detalhe.dados ? (
            <>
              <section className="cartao" aria-labelledby="h-par">
                <header><h2 id="h-par">Rodada {rodada}</h2>
                  <span><Badge>{rotuloTipoRodada(detalhe.dados.tipo)}</Badge> <Badge tipo={TIPO_ESTADO[detalhe.dados.estado] ?? ''}>{rotuloEstadoRodada(detalhe.dados.estado)}</Badge></span>
                </header>
                <div className="corpo">
                  {detalhe.dados.tipo === 'piloto' ? <Aviso tipo="alerta" titulo="Piloto">Conforme PRE-REGISTRO.md, os pilotos servem para depurar o instrumento e estimar duração; não entram na análise.</Aviso> : null}
                  {detalhe.dados.estado === 'interrompida' ? <Aviso tipo="erro" titulo="Coleta interrompida">Há experimento com status ‘executando’ e o arquivo não muda há {fmtHa(detalhe.dados.modificada_ha_s)}.</Aviso> : null}
                  {p ? (
                    <dl className="kv" style={{ marginTop: 8 }}>
                      <dt>Nível de compatibilidade</dt><dd><Num valor={p.compat} origem={{ rodada, tabela: 'rodada', metrica: 'compat' }} rotulo="compat" /></dd>
                      <dt>MAXDOP (escopo do banco)</dt><dd><Num valor={p.maxdop} origem={{ rodada, tabela: 'rodada', metrica: 'maxdop' }} rotulo="maxdop" /></dd>
                      <dt>Repetições quente / frio</dt><dd><Num valor={p.parametros?.n_quente} origem={{ rodada, tabela: 'rodada', metrica: 'parametros_json.n_quente' }} rotulo="repetições quente" /> / <Num valor={p.parametros?.n_frio} origem={{ rodada, tabela: 'rodada', metrica: 'parametros_json.n_frio' }} rotulo="repetições frio" /></dd>
                      <dt>Aquecimentos</dt><dd><Num valor={p.parametros?.aquecimentos} origem={{ rodada, tabela: 'rodada', metrica: 'parametros_json.aquecimentos' }} rotulo="aquecimentos" /></dd>
                      <dt>Iniciada</dt><dd>{fmtData(p.iniciada_utc)}</dd>
                    </dl>
                  ) : <p className="nota">Esta rodada não tem a tabela auxiliar <code>rodada</code> (não foi criada por coletar.py).</p>}
                </div>
              </section>
              <Experimentos detalhe={detalhe.dados} />
              <Agregado rodada={rodada} andamento={detalhe.dados.estado === 'em_andamento'} />
              <Runs rodada={rodada} />
            </>
          ) : null}
          <AoVivo />
        </div>
      </div>
    </div>
  );
}
