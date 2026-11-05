import { fmtNum, fmtRazao, rotuloMetrica, ehNumero } from '../lib/formatar.js';

const CORES = ['var(--serie-1)', 'var(--serie-2)', 'var(--serie-3)', 'var(--serie-4)', 'var(--serie-5)', 'var(--serie-6)'];

/** Escala log(1+x): aceita zero (muito comum em CPU em ms) sem descartar pontos. */
export const escalaLog = (v) => Math.log10(1 + Math.max(0, v));

export function ticksLog(max) {
  const t = [0];
  for (let p = 1; p <= Math.max(max, 1) * 1.0001; p *= 10) t.push(p);
  if (t[t.length - 1] < max) t.push(t[t.length - 1] * 10);
  return t;
}

/**
 * Grafico de pontos: uma coluna por variante, um ponto por repeticao medida, escala log(1+x).
 * `pontos`: [{ run_id, rep, variante, valor }] vindos da API (`/repeticoes`); `medianas`: { variante: mediana } vindas
 * do backend (nao se calcula estatistica aqui). Cada ponto tem <title> com run_id e repeticao (rastreio).
 */
export function GraficoPontos({ pontos, medianas = {}, metrica, titulo, ordem }) {
  const variantes = ordem ?? [...new Set(pontos.map((p) => p.variante))];
  if (!pontos.length) return null;
  const L = 520, A = 190, mE = 74, mD = 12, mT = 10, mB = 34;
  const max = Math.max(...pontos.map((p) => p.valor), ...Object.values(medianas).filter(ehNumero), 1);
  const ticks = ticksLog(max);
  const topo = escalaLog(ticks[ticks.length - 1]) || 1;
  const y = (v) => mT + (A - mT - mB) * (1 - escalaLog(v) / topo);
  const larg = (L - mE - mD) / variantes.length;
  const desc = `${titulo}: ${variantes.map((v) => `${v}, mediana ${ehNumero(medianas[v]) ? fmtNum(medianas[v], 0) : 'indisponível'}`).join('; ')}`;
  return (
    <svg className="grafico" viewBox={`0 0 ${L} ${A}`} role="img" aria-label={desc}>
      <title>{titulo}</title>
      <desc>{`Eixo vertical: ${rotuloMetrica(metrica)} em escala log(1+x). Cada ponto é uma repetição medida; o traço é a mediana calculada pelo backend.`}</desc>
      {ticks.map((t) => (
        <g key={t}>
          <line className="grade" x1={mE} x2={L - mD} y1={y(t)} y2={y(t)} />
          <text x={mE - 6} y={y(t) + 4} textAnchor="end">{fmtNum(t, 0)}</text>
        </g>
      ))}
      <line className="eixo" x1={mE} x2={mE} y1={mT} y2={A - mB} />
      <line className="eixo" x1={mE} x2={L - mD} y1={A - mB} y2={A - mB} />
      {variantes.map((v, i) => {
        const cx = mE + larg * (i + 0.5);
        const doGrupo = pontos.filter((p) => p.variante === v);
        const cor = CORES[i % CORES.length];
        return (
          <g key={v}>
            {doGrupo.map((p, k) => (
              <circle key={`${p.run_id}-${p.rep}`} cx={cx + ((k % 9) - 4) * Math.min(5, larg / 12)} cy={y(p.valor)} r="3" fill={cor} fillOpacity="0.75" stroke="var(--bg-2)" strokeWidth="0.5">
                <title>{`run ${p.run_id} · repetição ${p.rep} · ${rotuloMetrica(metrica)}: ${fmtNum(p.valor, 0)}`}</title>
              </circle>
            ))}
            {ehNumero(medianas[v]) ? (
              <line className="mediana" x1={cx - larg * 0.32} x2={cx + larg * 0.32} y1={y(medianas[v])} y2={y(medianas[v])}>
                <title>{`mediana de ${v}: ${fmtNum(medianas[v], 0)}`}</title>
              </line>
            ) : null}
            <text x={cx} y={A - mB + 14} textAnchor="middle">{v.length > 16 ? `${v.slice(0, 15)}…` : v}</text>
          </g>
        );
      })}
    </svg>
  );
}

/**
 * Razao depois/antes com IC (escala log). `itens`: [{ rotulo, razao, ic_lo, ic_hi, origem }].
 * Linha vertical em 1 (sem mudanca). Itens sem razao ou sem IC sao listados como indisponiveis, nunca desenhados.
 */
export function GraficoRazao({ itens, titulo }) {
  const validos = itens.filter((i) => ehNumero(i.razao_reg ?? i.razao) && ehNumero(i.ic_lo) && ehNumero(i.ic_hi));
  const L = 520, linha = 26, mE = 190, mD = 70, mT = 8, mB = 26;
  const A = mT + mB + Math.max(1, itens.length) * linha;
  const valores = validos.flatMap((i) => [i.ic_lo, i.ic_hi, i.razao_reg ?? i.razao, 1]).filter((v) => v > 0);
  const lo = Math.min(...valores, 1) * 0.8, hi = Math.max(...valores, 1) * 1.25;
  const x = (v) => mE + ((Math.log10(Math.max(v, lo)) - Math.log10(lo)) / (Math.log10(hi) - Math.log10(lo))) * (L - mE - mD);
  // marcas nas potencias de 10 dentro da escala, mais o 1 (sem mudanca); no maximo ~6 rotulos
  const decadas = [];
  for (let k = Math.ceil(Math.log10(lo)); 10 ** k <= hi; k += 1) decadas.push(10 ** k);
  const passo = Math.max(1, Math.ceil(decadas.length / 5));
  const marcas = [...new Set([...decadas.filter((_, i) => i % passo === 0), 1])];
  return (
    <svg className="grafico" viewBox={`0 0 ${L} ${A}`} role="img" aria-label={`${titulo}: ${itens.map((i) => `${i.rotulo} ${ehNumero(i.razao_reg ?? i.razao) ? fmtRazao(i.razao_reg ?? i.razao) : 'indisponível'}`).join('; ')}`}>
      <title>{titulo}</title>
      <desc>Razão depois/antes das medianas, com intervalo de confiança, em escala logarítmica. A linha vertical marca 1 (sem mudança); à esquerda dela, o depois é menor.</desc>
      {marcas.map((m) => (
        <g key={m}>
          <line className="grade" x1={x(m)} x2={x(m)} y1={mT} y2={A - mB} />
          <text x={x(m)} y={A - 8} textAnchor="middle">{m < 0.001 ? m.toExponential(0).replace('e-', 'e\u2212') : fmtRazao(m)}</text>
        </g>
      ))}
      <line x1={x(1)} x2={x(1)} y1={mT} y2={A - mB} stroke="var(--texto)" strokeWidth="1.5" />
      {itens.map((i, k) => {
        const yy = mT + linha * (k + 0.5);
        const r = i.razao_reg ?? i.razao;
        const ok = validos.includes(i);
        return (
          <g key={i.rotulo}>
            <text x={mE - 8} y={yy + 4} textAnchor="end">{i.rotulo}</text>
            {ok ? (
              <>
                <line x1={x(i.ic_lo)} x2={x(i.ic_hi)} y1={yy} y2={yy} stroke="var(--serie-1)" strokeWidth="3" strokeLinecap="round">
                  <title>{`IC: [${fmtRazao(i.ic_lo)}; ${fmtRazao(i.ic_hi)}] · ${i.origem ?? ''}`}</title>
                </line>
                <circle cx={x(r)} cy={yy} r="4.5" fill="var(--serie-2)" stroke="var(--bg-2)" strokeWidth="1">
                  <title>{`razão ${fmtRazao(r)} · ${i.origem ?? ''}`}</title>
                </circle>
                <text x={L - mD + 6} y={yy + 4}>{fmtRazao(r)}</text>
              </>
            ) : (
              <text x={mE + 4} y={yy + 4}>indisponível</text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

export function LegendaSeries({ itens }) {
  return (
    <div className="legenda" aria-hidden="true">
      {itens.map((n, i) => (
        <span key={n}><i style={{ background: CORES[i % CORES.length] }} />{n}</span>
      ))}
    </div>
  );
}
