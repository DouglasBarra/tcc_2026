// Painel (visao geral): dashboard do experimento no estilo de ferramentas de BI.
// Todos os numeros vem de /api/rodadas/{r}/analise (tcclab.analise) e de /api/rodadas/{r}; nada e calculado aqui
// alem de selecionar, ordenar e desenhar.
import { useMemo } from 'react';
import { useContexto } from '../contexto.jsx';
import { useApi } from '../hooks/useApi.js';
import { montarUrl, mensagemDeErro } from '../lib/api.js';
import { montarHash } from '../lib/roteador.js';
import { Aviso, Badge, Carregando, EstadoVazio } from '../componentes/EstadoVazio.jsx';
import { fmtNum, fmtP, fmtPct, rotuloMetrica } from '../lib/formatar.js';

const METRICAS = ['logical_reads', 'cpu_ms', 'elapsed_ms'];
/** Percentual com uma casa (o painel arredonda; os valores exatos estão na tela da consulta). */
const pct1 = (x) => (x == null ? 'n/d' : `${fmtNum(x, 1)} %`);
const BRACOS = [
  ['reescrita', 'Só a reescrita'],
  ['indice', 'Só o índice'],
  ['nativo', 'Índice sugerido pelo SQL Server'],
  ['prescricao_do_framework', 'Prescrição do framework'],
];

function agregado(lista, { grupo, metrica = 'logical_reads', estratoTipo = 'todas', estrato, regime }) {
  const cand = lista.filter((a) => a.grupo === grupo && a.metrica === metrica && a.regime === regime && a.estrato_tipo === estratoTipo && (estrato === undefined || a.estrato === estrato));
  return cand.find((a) => a.analise === 'principal') ?? cand.find((a) => a.analise === 'secundarias') ?? cand[0] ?? null;
}

/** Barras horizontais de reducao (%), com linha do limiar. Valores negativos (piora) vao para a esquerda do zero. */
function BarrasReducao({ itens, limiar, titulo, altura = 26, larg = 640, esq = 150 }) {
  const dir = 64; const util = larg - esq - dir; const topo = limiar != null ? 18 : 4;
  const min = Math.min(0, ...itens.map((i) => i.valor ?? 0));
  const x = (v) => esq + ((v - min) / (100 - min)) * util;
  const h = itens.length * altura + 24 + topo;
  return (
    <svg className="grafico barras" viewBox={`0 0 ${larg} ${h}`} role="img" aria-label={titulo}>
      {Array.from({ length: 5 }, (_, k) => k * 25).map((t) => (
        <g key={t}>
          <line className="grade" x1={x(t)} x2={x(t)} y1={topo} y2={h - 22} />
          <text x={x(t)} y={h - 6} textAnchor="middle">{t}%</text>
        </g>
      ))}
      {itens.map((i, k) => {
        const y = topo + k * altura;
        const v = i.valor ?? 0;
        const conteudo = (
          <>
            <text x={esq - 10} y={y + altura / 2 + 1} textAnchor="end" dominantBaseline="middle" className="rotulo-barra">{i.rotulo}</text>
            <rect className="trilha-barra" x={esq} y={y + 4} width={util} height={altura - 10} rx={4} />
            <rect className={`barra ${i.tom}`} x={Math.min(x(0), x(v))} y={y + 4} width={Math.max(2, Math.abs(x(v) - x(0)))} height={altura - 10} rx={4} />
            <text x={larg - dir + 8} y={y + altura / 2 + 1} dominantBaseline="middle" className="valor-barra">{pct1(i.valor)}</text>
            <title>{`${i.rotulo}${i.sub ? ` · ${i.sub}` : ''}: redução de ${pct1(i.valor)}`}</title>
          </>
        );
        return i.href ? <a key={i.rotulo} href={i.href}>{conteudo}</a> : <g key={i.rotulo}>{conteudo}</g>;
      })}
      {limiar != null ? (
        <g>
          <line className="limiar" x1={x(limiar)} x2={x(limiar)} y1={topo - 4} y2={h - 22} />
          <text className="limiar-rotulo" x={x(limiar)} y={topo - 7} textAnchor="middle">limiar {fmtPct(limiar, 0)}</text>
        </g>
      ) : null}
    </svg>
  );
}


/** Rótulo de uma potência de 10 em português (1, 10, 100, 1 mil, 10 mil, 1 mi...). */
function rotuloPot(e) {
  if (e >= 6) return `${10 ** (e - 6)} mi`;
  if (e >= 3) return `${10 ** (e - 3)} mil`;
  return `${10 ** e}`;
}

/** Halteres em escala log: páginas lidas antes (círculo vazio) e depois (círculo cheio), por consulta. */
function Halteres({ itens, titulo, larg = 760, esq = 56, altura = 32 }) {
  const dir = 84; const topo = 8; const util = larg - esq - dir;
  const vals = itens.flatMap((i) => [i.antes, i.depois]).filter((v) => v != null).map((v) => Math.max(1, v));
  const e0 = Math.floor(Math.log10(Math.min(...vals))); const e1 = Math.ceil(Math.log10(Math.max(...vals)));
  const x = (v) => esq + ((Math.log10(Math.max(1, v)) - e0) / Math.max(1, e1 - e0)) * util;
  const h = topo + itens.length * altura + 30;
  const ticks = Array.from({ length: e1 - e0 + 1 }, (_, k) => e0 + k);
  return (
    <svg className="grafico halteres" viewBox={`0 0 ${larg} ${h}`} role="img" aria-label={titulo}>
      {ticks.map((e) => (
        <g key={e}>
          <line className="grade" x1={x(10 ** e)} x2={x(10 ** e)} y1={topo} y2={h - 24} />
          <text x={x(10 ** e)} y={h - 6} textAnchor="middle">{rotuloPot(e)}</text>
        </g>
      ))}
      {itens.map((i, k) => {
        const y = topo + k * altura + altura / 2;
        const conteudo = (
          <>
            <rect className="linha-fundo" x={0} y={y - altura / 2} width={larg} height={altura} />
            <text x={esq - 14} y={y} textAnchor="end" dominantBaseline="middle" className="rotulo-barra">{i.rotulo}</text>
            <line className={`haste ${i.tom}`} x1={x(i.antes)} x2={x(i.depois)} y1={y} y2={y} />
            <circle className="antes" cx={x(i.antes)} cy={y} r={5} />
            <circle className={`depois ${i.tom}`} cx={x(i.depois)} cy={y} r={5.5} />
            <text x={larg - dir + 14} y={y} dominantBaseline="middle" className={`valor-barra ${i.tom}`}>{i.red == null ? 'n/d' : `−${fmtNum(i.red, 1)} %`}</text>
            <title>{`${i.rotulo}: ${fmtNum(i.antes, 0)} → ${fmtNum(i.depois, 0)} páginas (redução de ${pct1(i.red)})`}</title>
          </>
        );
        return i.href ? <a key={i.rotulo} href={i.href}>{conteudo}</a> : <g key={i.rotulo}>{conteudo}</g>;
      })}
    </svg>
  );
}

/** Anel de progresso (n de total). */
function Anel({ n, total, rotulo }) {
  const r = 36; const c = 2 * Math.PI * r; const frac = total ? n / total : 0;
  return (
    <svg className="anel" viewBox="0 0 88 88" role="img" aria-label={`${n} de ${total} ${rotulo}`}>
      <circle cx="44" cy="44" r={r} className="anel-fundo" />
      <circle cx="44" cy="44" r={r} className="anel-valor" strokeDasharray={`${c * frac} ${c}`} transform="rotate(-90 44 44)" />
      <text x="44" y="44" textAnchor="middle" dominantBaseline="central" className="anel-texto">{n}/{total}</text>
    </svg>
  );
}

function Kpi({ rotulo, valor, detalhe, tom = 'ok', icone }) {
  return (
    <div className={`kpi kpi-painel ${tom}`}>
      <span className="kpi-rotulo">{icone}{rotulo}</span>
      <span className={`kpi-valor ${tom}`}>{valor}</span>
      <span className="kpi-detalhe">{detalhe}</span>
    </div>
  );
}

export default function Painel() {
  const { rodada, rodadas, catalogo } = useContexto();
  const info = (rodadas.dados?.rodadas ?? []).find((r) => r.rodada === rodada);
  const concluida = info && info.estado !== 'em_andamento';
  const a = useApi(rodada ? montarUrl(`/api/rodadas/${rodada}/analise`, { calcular: concluida ? 'true' : undefined }) : null, { intervaloMs: 1500, continuar: (d) => d?.estado === 'calculando' });
  const det = useApi(rodada ? `/api/rodadas/${rodada}` : null);
  const descr = catalogo.dados?.descricoes_antipadrao ?? {};

  const r = a.dados?.estado === 'pronto' ? a.dados.resultado : null;
  const crit = r?.criterios;
  const regime = crit?.regime_principal ?? 'quente';
  const limiar = crit?.reducao_minima_pct ?? 20;

  const v = useMemo(() => {
    if (!r) return null;
    const ag = r.agregados ?? [];
    const metricas = METRICAS.map((m) => ({ m, a: agregado(ag, { grupo: 'prescricao_do_framework', metrica: m, regime }) }));
    const principais = (r.veredictos ?? []).filter((x) => x.regime === regime && x.comparacao === 'principal' && x.papel === 'principal');
    const porConsulta = [...principais].sort((p, q) => (q.reducao_pct ?? -1e9) - (p.reducao_pct ?? -1e9));
    const classes = [...new Set((r.consultas ?? []).map((c) => c.antipadrao))].filter(Boolean).sort().map((ap) => ({ ap, a: agregado(ag, { grupo: 'prescricao_do_framework', estratoTipo: 'antipadrao', estrato: ap, regime }) }));
    const bracos = BRACOS.map(([g, rot]) => ({ g, rot, a: agregado(ag, { grupo: g, regime }) })).filter((b) => b.a);
    const atingiram = principais.filter((x) => x.melhoria_significativa).length;
    return { metricas, porConsulta, classes, bracos, atingiram, total: principais.length };
  }, [r, regime]);

  if (!rodada) return <div className="pagina"><EstadoVazio titulo="Nenhuma rodada" motivo="Não há resultados em pratica/experimento/resultados." /></div>;
  const p = det.dados?.parametros_rodada;
  const lr = v?.metricas[0]?.a;

  return (
    <div className="pagina painel">
      <header className="painel-cab">
        <div>
          <span className="sobretitulo">Rodada {rodada}</span>
          <h1>Visão geral do experimento</h1>
          <p>O framework de quatro camadas foi aplicado a {v?.total ?? '…'} consultas com antipadrões no banco Stack Overflow 2010, em SQL Server 2022. Cada consulta foi medida antes e depois da prescrição.</p>
          <div className="painel-meta">
            {p ? <span>nível de compatibilidade {p.compat}</span> : null}
            {p ? <span>MAXDOP {p.maxdop}</span> : null}
            {p?.parametros ? <span>{p.parametros.n_quente} repetições no cenário quente e {p.parametros.n_frio} no frio</span> : null}
            <span>critério avaliado no cenário {regime}</span>
          </div>
        </div>
      </header>

      {a.erro ? <Aviso tipo="erro" titulo="Não foi possível carregar a análise">{mensagemDeErro(a.erro)}</Aviso> : null}
      {!r ? (
        a.dados?.estado === 'nao_calculada'
          ? <EstadoVazio titulo="Análise ainda não calculada" motivo="A rodada está em andamento; o cálculo entre consultas só roda quando ela termina (ou pela tela Histórico)." />
          : a.dados?.estado === 'erro' ? <Aviso tipo="erro" titulo="Falha na análise">{a.dados.motivo}</Aviso>
            : <div className="cartao"><div className="corpo"><Carregando texto="Calculando a análise entre consultas (bootstrap e testes)…" /></div></div>
      ) : (
        <>
          <section className="grade-kpi" aria-label="Redução mediana entre as consultas, prescrição contra o original">
            <h2 className="so-leitor">Redução mediana</h2>
            {v.metricas.map(({ m, a: x }) => (
              <Kpi
                key={m}
                rotulo={rotuloMetrica(m)}
                valor={x ? `−${pct1(x.reducao_mediana_pct)}` : 'n/d'}
                detalhe={x ? <>IC 95% [{pct1(x.reducao_ic_lo)}; {pct1(x.reducao_ic_hi)}] · p Holm {fmtP(x.p_holm)}</> : 'sem dados'}
                tom={x && x.reducao_mediana_pct >= limiar ? 'ok' : 'alerta'}
              />
            ))}
            <div className="kpi kpi-painel kpi-anel">
              <Anel n={v.atingiram} total={v.total} rotulo="consultas atingiram o critério" />
              <div className="kpi-anel-texto">
                <span className="kpi-rotulo">Atingiram o critério</span>
                <span className="kpi-detalhe">redução de {fmtPct(limiar, 0)} ou mais, com IC excluindo 1</span>
              </div>
            </div>
          </section>

          <section className={`aviso ${lr && lr.reducao_mediana_pct >= limiar ? 'ok' : 'alerta'} faixa-hipotese`}>
            <strong>{lr && lr.reducao_mediana_pct >= limiar ? 'Hipótese confirmada para o conjunto' : 'Hipótese não confirmada'}</strong>
            <span>
              A redução mediana das leituras lógicas foi de {pct1(lr?.reducao_mediana_pct)} entre {lr?.n_consultas ?? 'n/d'} consultas (Wilcoxon, p de Holm {lr ? fmtP(lr.p_holm) : 'n/d'}), contra o limiar de {fmtPct(limiar, 0)}.
              {v.total - v.atingiram === 1 ? ' Uma consulta ficou abaixo do critério individualmente.' : v.total - v.atingiram > 1 ? ` ${v.total - v.atingiram} consultas ficaram abaixo do critério individualmente.` : ''}
            </span>
          </section>

          <div className="grade-painel">
            <section className="cartao col-2" aria-labelledby="h-rank">
              <header>
                <h2 id="h-rank">Páginas lidas antes e depois, por consulta</h2>
                <span className="sub">mediana das leituras lógicas, escala logarítmica · clique numa linha para abrir a consulta</span>
              </header>
              <div className="corpo">
                <Halteres
                  titulo="Páginas lidas antes e depois da prescrição, por consulta, em escala logarítmica"
                  itens={v.porConsulta.map((x) => ({
                    rotulo: x.consulta,
                    antes: x.mediana_antes,
                    depois: x.mediana_depois,
                    red: x.reducao_pct,
                    tom: x.piora ? 'erro' : x.melhoria_significativa ? 'ok' : 'alerta',
                    href: montarHash('consulta', x.consulta, { rodada, aba: 'validacao' }),
                  }))}
                />
                <div className="legenda">
                  <span><i className="leg-antes" />antes (original)</span>
                  <span><i style={{ background: 'var(--acento)' }} />depois, atingiu o critério</span>
                  <span><i style={{ background: 'var(--acento-2)' }} />depois, abaixo do critério</span>
                </div>
              </div>
            </section>

            <div className="pilha">
              <section className="cartao" aria-labelledby="h-classe">
                <header><h2 id="h-classe">Por classe de antipadrão</h2><span className="sub">redução mediana das leituras lógicas</span></header>
                <div className="corpo">
                  <BarrasReducao
                    titulo="Redução mediana por classe de antipadrão"
                    limiar={limiar}
                    altura={36}
                    larg={500}
                    esq={56}
                    itens={v.classes.map(({ ap, a: x }) => ({ rotulo: ap, sub: descr[ap], valor: x?.reducao_mediana_pct ?? null, tom: x && x.reducao_mediana_pct >= limiar ? 'ok' : 'alerta' }))}
                  />
                  <ul className="lista-classes">
                    {v.classes.map(({ ap, a: x }) => (
                      <li key={ap}><strong>{ap}</strong> <span className="sub">{descr[ap] ?? ''}</span> <span className="sub">· n = {x?.n_consultas ?? 'n/d'}</span></li>
                    ))}
                  </ul>
                </div>
              </section>

              <section className="cartao" aria-labelledby="h-braco">
                <header><h2 id="h-braco">O que cada recomendação contribui</h2><span className="sub">leituras lógicas</span></header>
                <div className="corpo">
                  <BarrasReducao
                    titulo="Redução mediana por braço do experimento"
                    altura={36}
                    larg={540}
                    esq={176}
                    itens={v.bracos.map(({ g, rot, a: x }) => ({ rotulo: rot.replace('Índice sugerido pelo SQL Server', 'Índice nativo'), sub: `n = ${x.n_consultas}`, valor: x.reducao_mediana_pct, tom: g === 'prescricao_do_framework' ? 'acento' : 'neutro' }))}
                  />
                  <p className="sub" style={{ margin: '8px 0 0' }}>Medianas entre as consultas em que cada braço existe; a reescrita isolada só existe nas consultas com reescrita.</p>
                </div>
              </section>
            </div>
          </div>

          <section className="cartao" aria-labelledby="h-tab">
            <header><h2 id="h-tab">Tabela das consultas principais</h2><span className="sub">mediana antes e depois · {rotuloMetrica('logical_reads').toLowerCase()}</span></header>
            <div className="corpo sem-padding rolagem">
              <table className="tabela">
                <thead><tr><th scope="col">Consulta</th><th scope="col">Classe</th><th scope="col" className="n">Antes</th><th scope="col" className="n">Depois</th><th scope="col" style={{ width: '28%' }}>Redução</th><th scope="col">Critério</th></tr></thead>
                <tbody>
                  {v.porConsulta.map((x) => (
                    <tr key={x.consulta}>
                      <th scope="row"><a href={montarHash('consulta', x.consulta, { rodada, aba: 'validacao' })}>{x.consulta}</a></th>
                      <td className="mono">{x.antipadrao}</td>
                      <td className="n">{fmtNum(x.mediana_antes, 0)}</td>
                      <td className="n">{fmtNum(x.mediana_depois, 0)}</td>
                      <td>
                        <div className="medidor" title={pct1(x.reducao_pct)}>
                          <span className={`medidor-barra ${x.melhoria_significativa ? 'ok' : 'alerta'}`} style={{ width: `${Math.max(0, Math.min(100, x.reducao_pct ?? 0))}%` }} />
                          <span className="medidor-valor">{pct1(x.reducao_pct)}</span>
                        </div>
                      </td>
                      <td><Badge tipo={x.melhoria_significativa ? 'ok' : x.piora ? 'erro' : 'alerta'}>{x.melhoria_significativa ? 'atingiu' : x.piora ? 'piorou' : 'abaixo'}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <p className="nota">Análise gerada por tcclab.analise ({r.meta?.versao_analise ?? 'n/d'}) em {r.meta?.gerado_em_utc ?? 'n/d'} UTC. Passe o mouse sobre as barras para ver os valores.</p>
        </>
      )}
    </div>
  );
}
