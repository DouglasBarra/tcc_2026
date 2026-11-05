import { useMemo, useState } from 'react';
import { useApi } from '../../hooks/useApi.js';
import { montarUrl, mensagemDeErro } from '../../lib/api.js';
import { Aviso, Badge, Carregando, EstadoVazio } from '../../componentes/EstadoVazio.jsx';
import { GraficoPontos, GraficoRazao, LegendaSeries } from '../../componentes/Graficos.jsx';
import { Num } from '../../componentes/Num.jsx';
import { fmtData, fmtNum, fmtPct, rotuloMetrica, rotuloRegime, rotuloEstadoRodada } from '../../lib/formatar.js';
import { acharVeredito, baseline, itensRazao, limiteDeRazao, linhasComparacao, regimesDisponiveis, tratamentos } from '../../lib/validacao.js';
import TabelaComparacao from './TabelaComparacao.jsx';
import CustoEscrita from './CustoEscrita.jsx';
import ValidacaoAoVivo from './ValidacaoAoVivo.jsx';
import RegistroColeta from './RegistroColeta.jsx';

const METRICAS_GRAFICO = ['logical_reads', 'cpu_ms', 'elapsed_ms', 'cpu_us', 'elapsed_us', 'physical_reads'];

/** Cartao do criterio pre-registrado: numeros lidos de tcclab.analise.Criterios, nunca fixos na interface. */
function CriterioPreRegistrado({ validacao, rodada }) {
  const a = validacao.analise;
  if (!a.disponivel) {
    return <EstadoVazio titulo="Parâmetros do critério indisponíveis" motivo={`${a.motivo}. O critério está descrito em experimento/PRE-REGISTRO.md; a interface não o avalia por conta própria.`} />;
  }
  const c = a.criterios;
  const lim = limiteDeRazao(c);
  const o = (campo) => ({ rodada, tabela: 'tcclab.analise.Criterios', metrica: campo, nota: `critérios ${a.criterios_sha}` });
  return (
    <p style={{ margin: 0 }}>
      <strong>Critério de melhoria por consulta</strong> (experimento/PRE-REGISTRO.md): razão das medianas de {rotuloMetrica(c.metrica_criterio).toLowerCase()} depois/antes{' '}
      ≤ <Num valor={lim} formato={(x) => fmtNum(x, 2)} origem={o('reducao_minima_pct (1 − reducao/100)')} rotulo="limite da razão" />{' '}
      (redução ≥ <Num valor={c.reducao_minima_pct} formato={(x) => fmtPct(x, 0)} origem={o('reducao_minima_pct')} rotulo="redução mínima" />) no regime <strong>{c.regime_principal}</strong>,
      com IC de <Num valor={c.nivel_ic * 100} formato={(x) => fmtPct(x, 0)} origem={o('nivel_ic')} rotulo="nível do IC" /> da razão excluindo 1. O outro regime é reportado à parte e não entra no critério.
    </p>
  );
}

function Criterio({ validacao, depois, regime }) {
  const a = validacao.analise;
  if (!a.disponivel) return <EstadoVazio titulo="Critério de melhoria indisponível" motivo={`${a.motivo}. A interface não avalia o critério por conta própria.`} />;
  const v = acharVeredito(validacao, regime, depois);
  if (!v) return <EstadoVazio titulo="Sem veredito" motivo={`tcclab.analise não produziu veredito para ${depois} no regime ${regime}.`} />;
  const c = a.criterios;
  const lim = limiteDeRazao(c);
  return (
    <div className="chips" aria-label={`Critério de melhoria, regime ${regime}`}>
      <Badge tipo={v.reducao_ok ? 'ok' : 'erro'}>{v.reducao_ok ? '✔' : '✘'} razão ≤ {fmtNum(lim, 2)} em {rotuloMetrica(v.metrica_criterio).toLowerCase()}</Badge>
      <Badge tipo={v.ic_exclui_1 ? 'ok' : 'erro'}>{v.ic_exclui_1 ? '✔' : '✘'} IC {fmtPct(c.nivel_ic * 100, 0)} da razão exclui 1</Badge>
      <Badge tipo={v.melhoria_significativa ? 'ok' : 'alerta'}>melhoria significativa: {v.melhoria_significativa ? 'sim' : 'não'}</Badge>
      {v.piora ? <Badge tipo="erro">piora: razão &gt; 1 (efeito colateral que reabre o Diagnóstico)</Badge> : null}
      {v.veredito_muda_sem_atipicos ? <Badge tipo="alerta">veredito muda sem os atípicos</Badge> : null}
    </div>
  );
}


/** Resumo em destaque: as tres metricas principais no regime do criterio, antes -> depois e reducao. */
function Resumo({ validacao, regime, depois, base }) {
  const v = acharVeredito(validacao, regime, depois);
  const linhas = linhasComparacao(validacao, regime, depois).filter((l) => !l.secundaria);
  if (!linhas.length) return null;
  const fmtValor = (m, x) => (x == null ? 'n/d' : m === 'logical_reads' ? fmtNum(x, 0) : `${fmtNum(x, x < 10 ? 2 : 0)} ms`);
  return (
    <section className="resumo" aria-label={`Resumo do resultado, regime ${regime}`}>
      <div className="resumo-veredito">
        {v ? (
          <span className={`veredito ${v.melhoria_significativa ? 'ok' : v.piora ? 'erro' : 'alerta'}`}>
            {v.melhoria_significativa ? 'Melhoria significativa' : v.piora ? 'A consulta piorou' : 'Abaixo do critério'}
          </span>
        ) : null}
        <span className="sub">{base?.nome ?? 'original'} → {depois} · regime {rotuloRegime(regime).toLowerCase()}</span>
      </div>
      <div className="resumo-cartoes">
        {linhas.map((l) => {
          const red = l.comp?.reducao_pct;
          const ruim = red != null && red < 0;
          return (
            <div key={l.metrica} className="kpi">
              <span className="kpi-rotulo">{rotuloMetrica(l.metrica)}</span>
              <span className={`kpi-valor ${ruim ? 'erro' : 'ok'}`}>{red == null ? 'n/d' : `${red > 0 ? '−' : '+'}${fmtPct(Math.abs(red), 1)}`}</span>
              <span className="kpi-detalhe">{fmtValor(l.metrica, l.antes?.mediana)} → {fmtValor(l.metrica, l.depois?.mediana)}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

export default function Validacao({ id, rodada, validacao, escrita, estado, detalhe, textoOriginal, textoDepois, ddlDepois, onRodada, params, setParams }) {
  const dados = validacao.dados;
  const [metrica, setMetrica] = useState('logical_reads');
  const [depoisEscolhido, setDepois] = useState(null);
  const trats = useMemo(() => tratamentos(dados), [dados]);
  const depois = depoisEscolhido && trats.some((t) => t.nome === depoisEscolhido) ? depoisEscolhido : dados?.tratamento_padrao ?? trats[0]?.nome ?? null;
  const expId = dados?.experimento?.experiment_id;
  const rep = useApi(dados?.disponivel && expId ? montarUrl(`/api/rodadas/${rodada}/consultas/${id}/repeticoes`, { metrica, experimento: expId }) : null);

  if (validacao.carregando && !dados) return <Carregando texto="Carregando medições…" />;
  if (validacao.erro) return <Aviso tipo="erro" titulo="Não foi possível carregar a Validação">{mensagemDeErro(validacao.erro)}</Aviso>;
  if (!rodada) return <EstadoVazio titulo="Nenhuma rodada selecionada" motivo="Não há SQLite de resultados em pratica/experimento/resultados. Rode a coleta (experimento/coletar.py) ou use a validação ao vivo." />;

  const vivo = (
    <ValidacaoAoVivo
      estado={estado} antes={textoOriginal} depois={textoDepois} ddl={ddlDepois} queryId={id} tabelas={detalhe?.tabelas}
      onConcluir={(j) => onRodada?.(j.rodada)}
    />
  );
  if (!dados?.disponivel) {
    return (
      <>
        <EstadoVazio titulo="Esta consulta não tem medições na rodada selecionada" motivo={`${dados?.motivo ?? ''} Rodada: ${rodada}.`}>
          <span className="sub">Escolha outra rodada no topo ou rode a coleta para esta consulta. Nenhum valor é preenchido sem registro de medição.</span>
        </EstadoVazio>
        {dados?.registro_coleta?.length ? (
          <section className="cartao" aria-labelledby="h-reg0">
            <header><h3 id="h-reg0">Registro da coleta</h3></header>
            <div className="corpo sem-padding"><RegistroColeta validacao={dados} rodada={rodada} /></div>
          </section>
        ) : null}
        {vivo}
      </>
    );
  }

  const exp = dados.experimento;
  const regimes = regimesDisponiveis(dados);
  const base = baseline(dados);
  const eq = dados.coleta?.equivalencia;
  const medianas = (regime) => Object.fromEntries(dados.grupos.filter((g) => g.regime === regime && g.metrica === metrica).map((g) => [g.variante, g.mediana]));
  const principal = dados.analise.criterios?.regime_principal && regimes.includes(dados.analise.criterios.regime_principal) ? dados.analise.criterios.regime_principal : regimes[0];
  const linhasPorRegime = Object.fromEntries(regimes.map((r) => [r, linhasComparacao(dados, r, depois)]));

  return (
    <>
      <div className="campo-linha">
        <label className="campo">
          <span>Depois (variante comparada com {base?.nome ?? 'a original'})</span>
          <select name="selecao" value={depois ?? ''} onChange={(e) => setDepois(e.target.value)}>
            {trats.map((t) => <option key={t.variant_id} value={t.nome}>{t.nome}{t.nome === dados.tratamento_padrao ? ' (prescrição)' : ''}</option>)}
          </select>
        </label>
        {dados.experimentos.length > 1 ? (
          <label className="campo">
            <span>Experimento</span>
            <select name="selecao" value={expId} onChange={(e) => setParams({ exp: e.target.value })}>
              {dados.experimentos.map((x) => <option key={x.experiment_id} value={x.experiment_id}>{x.experiment_id} · tentativa {x.tentativa ?? 'n/d'}{x.descartado_por_deriva ? ' (descartado por deriva)' : ''}</option>)}
            </select>
          </label>
        ) : null}
        <div className="chips">
          <Badge tipo={exp.status === 'concluido' ? 'ok' : 'alerta'}>experimento {exp.experiment_id}: {exp.status}</Badge>
          <Badge>{fmtData(exp.started_at)}</Badge>
          {dados.tipo_rodada === 'piloto' ? <Badge tipo="alerta" titulo="PRE-REGISTRO.md: pilotos não entram na análise">piloto: não entra na análise</Badge> : null}
          {dados.tipo_rodada === 'ao_vivo' ? <Badge tipo="info">validação ao vivo</Badge> : null}
          {eq ? <Badge tipo={eq.ok ? 'ok' : 'erro'} titulo={`coleta_consulta: ${JSON.stringify(eq.hash_referencia ?? '')}`}>{eq.aplicavel === false ? 'sem reescrita' : eq.ok ? 'equivalência de resultado: ✔' : 'equivalência: ✘'}</Badge> : null}
        </div>
      </div>

      <Resumo validacao={dados} regime={principal} depois={depois} base={base} />

      {regimes.filter((r) => r === principal).map((r) => {
        const doCriterio = dados.analise.criterios?.regime_principal === r;
        return (
          <section key={r} className="cartao" aria-labelledby={`h-reg-${r}`}>
            <header>
              <h3 id={`h-reg-${r}`}>Regime {rotuloRegime(r).toLowerCase()} {doCriterio ? <Badge tipo="acento">regime do critério</Badge> : <Badge>reportado à parte: fora do critério</Badge>}</h3>
              <span className="sub">{r === 'frio' ? 'frio apenas para o buffer pool do SQL Server (CHECKPOINT + DROPCLEANBUFFERS), não para o disco' : 'após aquecimento; plano em cache'}</span>
            </header>
            <div className="corpo sem-padding">
              <TabelaComparacao linhas={linhasPorRegime[r]} regimeRotulo={rotuloRegime(r)} antesNome={base?.nome ?? 'n/d'} depoisNome={depois ?? 'n/d'} rodada={rodada} experimentId={expId} />
            </div>
            <div className="corpo">
              <strong className="sub">{doCriterio ? 'Veredito do critério:' : 'Veredito informativo (não entra no critério):'}</strong>{' '}
              <Criterio validacao={dados} depois={depois} regime={r} />
            </div>
          </section>
        );
      })}

      <section className="cartao" aria-labelledby="h-graf">
        <header>
          <h3 id="h-graf">Distribuição das repetições e razão com IC</h3>
          <label className="campo"><span className="so-leitor">Métrica do gráfico de pontos</span>
            <select name="selecao" value={metrica} onChange={(e) => setMetrica(e.target.value)} aria-label="Métrica do gráfico de pontos">
              {METRICAS_GRAFICO.map((m) => <option key={m} value={m}>{rotuloMetrica(m)}</option>)}
            </select>
          </label>
        </header>
        {regimes.filter((r) => r === principal).map((r) => (
          <div key={r} className="corpo grade-2">
            <figure style={{ margin: 0 }}>
              <figcaption className="sub">Regime {rotuloRegime(r).toLowerCase()}: {rotuloMetrica(metrica)} por repetição (escala log(1+x); traço = mediana)</figcaption>
              {rep.dados?.disponivel ? (
                <GraficoPontos
                  pontos={rep.dados.pontos.filter((p) => p.regime === r)}
                  medianas={medianas(r)} metrica={metrica} titulo={`${rotuloMetrica(metrica)}, regime ${r}`}
                  ordem={dados.variantes.map((v) => v.nome).filter((n) => rep.dados.pontos.some((p) => p.regime === r && p.variante === n))}
                />
              ) : rep.carregando ? <Carregando /> : <EstadoVazio titulo="Sem repetições para o gráfico" motivo={rep.dados?.motivo ?? mensagemDeErro(rep.erro)} />}
            </figure>
            <figure style={{ margin: 0 }}>
              <figcaption className="sub">Regime {rotuloRegime(r).toLowerCase()}: razão {depois}/{base?.nome} com IC (escala log; à esquerda de 1 = redução)</figcaption>
              <GraficoRazao itens={itensRazao(linhasPorRegime[r], rodada, expId, rotuloMetrica)} titulo={`Razão ${depois}/${base?.nome}, regime ${r}`} />
            </figure>
          </div>
        ))}
        <div className="corpo"><LegendaSeries itens={dados.variantes.map((v) => v.nome)} /></div>
      </section>

      <details className="detalhes-tec">
        <summary>Detalhes técnicos <span className="sub">critério pré-registrado, outro regime, avisos ({dados.avisos.length}), determinismo e registro da coleta</span></summary>
        <div className="detalhes-corpo">
      {dados.avisos.length ? (
        <Aviso tipo="alerta" titulo="Avisos do registro">
          <ul>{dados.avisos.map((a) => <li key={a}>{a}</li>)}</ul>
        </Aviso>
      ) : null}
      {!dados.analise.disponivel ? (
        <Aviso tipo="info" titulo="Razão, IC e testes indisponíveis">{dados.analise.motivo}. Estão sendo mostradas apenas estatísticas descritivas (tcclab.stats).</Aviso>
      ) : null}

      <section className="cartao" aria-labelledby="h-crit">
        <header><h3 id="h-crit">Critério usado</h3></header>
        <div className="corpo"><CriterioPreRegistrado validacao={dados} rodada={rodada} /></div>
      </section>

      {regimes.filter((r) => r !== principal).map((r) => (
        <section key={r} className="cartao" aria-labelledby={`h-reg-${r}`}>
          <header>
            <h3 id={`h-reg-${r}`}>Regime {rotuloRegime(r).toLowerCase()} <Badge>reportado à parte: fora do critério</Badge></h3>
            <span className="sub">{r === 'frio' ? 'frio apenas para o buffer pool do SQL Server (CHECKPOINT + DROPCLEANBUFFERS), não para o disco' : 'após aquecimento; plano em cache'}</span>
          </header>
          <div className="corpo sem-padding">
            <TabelaComparacao linhas={linhasPorRegime[r]} regimeRotulo={rotuloRegime(r)} antesNome={base?.nome ?? 'n/d'} depoisNome={depois ?? 'n/d'} rodada={rodada} experimentId={expId} />
          </div>
          <div className="corpo"><strong className="sub">Veredito informativo (não entra no critério):</strong> <Criterio validacao={dados} depois={depois} regime={r} /></div>
        </section>
      ))}
      <section className="cartao" aria-labelledby="h-det">
        <header><h3 id="h-det">Determinismo e blocos</h3><span className="sub">leituras lógicas devem ter desvio padrão zero com plano e dados fixos</span></header>
        <div className="corpo sem-padding rolagem">
          <table className="tabela">
            <thead><tr><th scope="col">Run</th><th scope="col">Regime</th><th scope="col">Bloco</th><th scope="col">Variante</th><th scope="col">Status</th><th scope="col" className="n">n</th><th scope="col" className="n">Mediana lógicas</th><th scope="col" className="n">Mediana CPU (ms)</th><th scope="col">QueryPlanHash</th></tr></thead>
            <tbody>
              {dados.runs.map((r) => (
                <tr key={r.run_id}>
                  <th scope="row" className="mono">{r.run_id}</th>
                  <td>{rotuloRegime(r.regime)}</td><td className="mono">{r.block}</td><td>{r.variante}</td>
                  <td><Badge tipo={r.status === 'concluido' ? 'ok' : 'alerta'}>{r.status}</Badge>{r.avisos?.length ? <Badge tipo="alerta" titulo={r.avisos.join('; ')}>aviso</Badge> : null}</td>
                  <td className="n"><Num valor={r.n} origem={{ rodada, experiment_id: expId, run_ids: [r.run_id], tabela: 'measurement', nota: 'repetições medidas' }} rotulo="n" /></td>
                  <td className="n"><Num valor={r.medianas.logical_reads} origem={{ rodada, experiment_id: expId, run_ids: [r.run_id], tabela: 'measurement', metrica: 'logical_reads', n: r.n, fonte: 'tcclab.stats' }} rotulo="mediana de leituras lógicas" /></td>
                  <td className="n"><Num valor={r.medianas.cpu_ms} origem={{ rodada, experiment_id: expId, run_ids: [r.run_id], tabela: 'measurement', metrica: 'cpu_ms', n: r.n, fonte: 'tcclab.stats' }} rotulo="mediana de CPU" /></td>
                  <td className="mono">{r.query_plan_hash ?? 'n/d'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="corpo">
          {dados.determinismo.length ? (
            <div className="chips">
              {dados.determinismo.map((d) => (
                <Badge key={`${d.variante}-${d.regime}`} tipo={d.deterministico ? 'ok' : 'alerta'} titulo={d.alerta || ''}>{d.variante} · {d.regime}: {d.deterministico ? 'determinística' : 'dp > 0'}</Badge>
              ))}
            </div>
          ) : null}
        </div>
      </section>

      <section className="cartao" aria-labelledby="h-reg">
        <header><h3 id="h-reg">Registro da coleta</h3><span className="sub">tentativas, descartes por deriva, equivalência e braço nativo</span></header>
        <div className="corpo sem-padding"><RegistroColeta validacao={dados} rodada={rodada} /></div>
      </section>

        </div>
      </details>

      <section className="cartao" aria-labelledby="h-esc">
        <header><h3 id="h-esc">Custo de escrita do índice prescrito</h3></header>
        <div className="corpo sem-padding"><CustoEscrita escrita={escrita} rodada={rodada} /></div>
      </section>

      {vivo}
      <p className="nota">Rodada {rodada} ({rotuloEstadoRodada(exp.status)}). Cada número tem origem: passe o mouse ou foque nele para ver rodada, experimento, runs e coluna de {`measurement`}.</p>
    </>
  );
}
