import { useState } from 'react';
import { Num } from '../../componentes/Num.jsx';
import { EstadoVazio } from '../../componentes/EstadoVazio.jsx';
import { fmtIC, fmtInt, fmtPct, fmtRazao, rotuloMetrica } from '../../lib/formatar.js';
import { origemCurta } from '../../lib/origem.js';
import { notaRegraZero, origemDoGrupo, variacaoPct } from '../../lib/validacao.js';

function Lado({ g, rodada, exp }) {
  if (!g) return <span className="sub">sem dado</span>;
  const o = origemDoGrupo(g, rodada, exp);
  return (
    <>
      <Num valor={g.mediana} origem={{ ...o, nota: 'mediana' }} rotulo="mediana" />
      <div className="sub">
        IQR [<Num valor={g.q1} origem={{ ...o, nota: 'quartil 1' }} rotulo="Q1" />; <Num valor={g.q3} origem={{ ...o, nota: 'quartil 3' }} rotulo="Q3" />] · n=<Num valor={g.n} origem={{ ...o, nota: 'repetições medidas' }} rotulo="n" />
      </div>
    </>
  );
}

/**
 * Tabela antes x depois de UM regime. `linhas` vem de lib/validacao.linhasComparacao.
 * Sem linhas (sem grupos para o regime) => estado vazio, nunca uma tabela de zeros.
 */
export default function TabelaComparacao({ linhas, regimeRotulo, antesNome, depoisNome, rodada, experimentId }) {
  if (!linhas.length) {
    return <EstadoVazio titulo={`Sem medições no regime ${regimeRotulo.toLowerCase()}`} motivo="Este experimento não registrou repetições medidas neste regime." />;
  }
  const [completo, setCompleto] = useState(false);
  const nSec = linhas.filter((l) => l.secundaria).length;
  const visiveis = completo ? linhas : linhas.filter((l) => !l.secundaria);
  return (
    <div className="rolagem">
      {(
        <div className="tabela-alternar">
          <button type="button" className="btn discreto" aria-expanded={completo} onClick={() => setCompleto(!completo)}>
            {completo ? 'Mostrar a versão resumida' : nSec ? `Mostrar tudo (+${nSec} métricas secundárias e origem de cada número)` : 'Mostrar a origem de cada número'}
          </button>
        </div>
      )}
      <table className="tabela">
        <caption>Regime {regimeRotulo.toLowerCase()}: mediana por variante (antes = {antesNome}; depois = {depoisNome}).</caption>
        <thead>
          <tr>
            <th scope="col">Métrica</th>
            <th scope="col">Antes ({antesNome})</th>
            <th scope="col">Depois ({depoisNome})</th>
            <th scope="col" className="n">Razão depois/antes</th>
            <th scope="col" className="n">IC 95 % da razão</th>
            <th scope="col" className="n">Variação da mediana</th>
            {completo ? <th scope="col">Origem</th> : null}
          </tr>
        </thead>
        <tbody>
          {visiveis.map((l) => {
            const c = l.comp;
            const orig = c ? { rodada, experiment_id: c.experiment_id, run_ids: [...c.run_ids_antes, ...c.run_ids_depois], tabela: 'measurement', metrica: l.metrica, n: c.n_antes + c.n_depois, fonte: 'tcclab.analise' } : null;
            const razao = c ? (c.razao ?? c.razao_reg) : null;
            const nota = notaRegraZero(c);
            return (
              <tr key={l.metrica} className={l.secundaria ? 'secundaria' : ''}>
                <th scope="row">{rotuloMetrica(l.metrica)}{!completo && c?.deriva_alerta ? <div><span className="badge alerta" title="mediana do último bloco da linha de base contra o primeiro (A2 contra A1)">deriva A1→A2 {fmtPct(c.deriva_base_pct, 1, true)}</span></div> : null}</th>
                <td><Lado g={l.antes} rodada={rodada} exp={experimentId} /></td>
                <td><Lado g={l.depois} rodada={rodada} exp={experimentId} /></td>
                <td className="n">
                  <Num valor={razao} formato={fmtRazao} origem={orig ? { ...orig, nota: c.razao === null ? 'razão regularizada (antes = 0)' : 'razão das medianas' } : ''} rotulo="razão" />
                  {c?.razao === null && c?.razao_reg != null ? <span className="sub" title="razão regularizada: a razão bruta é indefinida"> ≥</span> : null}
                </td>
                <td className="n">
                  {c && c.ic_lo != null && c.ic_hi != null ? (
                    <>[<Num valor={c.ic_lo} formato={fmtRazao} origem={{ ...orig, nota: 'limite inferior do IC (bootstrap percentil)' }} rotulo="IC inferior" />; <Num valor={c.ic_hi} formato={fmtRazao} origem={{ ...orig, nota: 'limite superior do IC (bootstrap percentil)' }} rotulo="IC superior" />]</>
                  ) : (
                    <span className="sub" title="IC vem de tcclab.analise">{fmtIC(null, null)}</span>
                  )}
                </td>
                <td className="n">
                  <Num valor={variacaoPct(c)} formato={(x) => fmtPct(x, 1, true)} origem={orig ? { ...orig, nota: 'variação = −redução' } : ''} rotulo="variação" />
                </td>
                {completo ? <td className="origem">
                  {origemCurta({ experiment_id: experimentId, run_ids: [...(l.antes?.run_ids ?? []), ...(l.depois?.run_ids ?? [])] }) || 'n/d'}
                  {c?.deriva_alerta ? <div><span className="badge alerta" title="mediana do último bloco da linha de base contra o primeiro (A2 contra A1)">deriva A1→A2 {fmtPct(c.deriva_base_pct, 1, true)}: acima do limite</span></div> : null}
                  {c && (c.atipicos_antes > 0 || c.atipicos_depois > 0) ? <div className="sub">atípicos: {fmtInt(c.atipicos_antes)} antes, {fmtInt(c.atipicos_depois)} depois (sinalizados, nunca descartados)</div> : null}
                  {nota ? <div className="sub">{nota}</div> : null}
                </td> : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
