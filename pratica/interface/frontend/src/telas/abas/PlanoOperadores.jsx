import { useMemo, useState } from 'react';
import { Num } from '../../componentes/Num.jsx';
import { Aviso, EstadoVazio } from '../../componentes/EstadoVazio.jsx';
import { fmtInt, fmtNum, fmtPct, fmtRazao, VAZIO } from '../../lib/formatar.js';
import { IconeSeta } from '../../componentes/Icones.jsx';

export const COLUNAS = [
  ['node_id', 'Nó', false],
  ['operador_fisico', 'Operador', false],
  ['objeto', 'Objeto (índice)', false],
  ['linhas_estimadas', 'Linhas est.', true],
  ['linhas_reais', 'Linhas reais', true],
  ['execucoes', 'Execuções', true],
  ['leituras_logicas', 'Leituras lógicas', true],
  ['custo_subarvore', 'Custo est. da subárvore', true],
  ['razao_estimado_real', 'Est./real', true],
];

export function ordenar(operadores, chave, desc) {
  const v = (o) => o[chave];
  return [...operadores].sort((a, b) => {
    const x = v(a), y = v(b);
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1; // sem dado sempre por ultimo
    const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), 'pt-BR');
    return desc ? -c : c;
  });
}

/** Tabela de operadores de um plano (campos brutos do XML; ordenacao e so apresentacao). */
export default function PlanoOperadores({ plano, origem }) {
  const [chave, setChave] = useState('node_id');
  const [desc, setDesc] = useState(false);
  const ops = plano?.operadores ?? [];
  const linhas = useMemo(() => ordenar(ops, chave, desc), [ops, chave, desc]);
  if (!plano?.valido) return <EstadoVazio titulo="Plano ilegível" motivo={plano?.motivo} />;
  const real = plano.eh_plano_real;
  const o = (extra) => ({ ...origem, ...extra });
  const alternar = (c) => { if (c === chave) setDesc(!desc); else { setChave(c); setDesc(c !== 'node_id' && c !== 'operador_fisico' && c !== 'objeto'); } };
  return (
    <>
      <div className="chips">
        <span className="badge">{real ? 'plano real' : 'plano estimado'}</span>
        <span className="badge" title="QueryPlanHash do servidor">QueryPlanHash <span className="mono">{plano.query_plan_hash ?? VAZIO}</span></span>
        <span className="badge">CE {plano.versao_ce ?? VAZIO}</span>
        <span className="badge">DOP <Num valor={plano.grau_paralelismo} origem={o({ nota: 'DegreeOfParallelism do plano' })} rotulo="grau de paralelismo" /></span>
        <span className="badge">custo da instrução <Num valor={plano.custo_subarvore} formato={(x) => fmtNum(x, 3)} origem={o({ nota: 'StatementSubTreeCost' })} rotulo="custo da instrução" /></span>
        {real ? <span className="badge">leituras lógicas do plano <Num valor={plano.leituras_logicas_plano} origem={o({ nota: 'soma de ActualLogicalReads' })} rotulo="leituras lógicas do plano" /></span> : null}
      </div>
      <div className="rolagem">
        <table className="tabela">
          <caption>Operadores do plano (ordenável). Campos exatamente como no XML; “Est./real” = linhas estimadas ÷ linhas reais.</caption>
          <thead>
            <tr>
              {COLUNAS.map(([c, rot, num]) => (
                <th key={c} scope="col" className={num ? 'n' : ''} aria-sort={chave === c ? (desc ? 'descending' : 'ascending') : 'none'}>
                  <button type="button" onClick={() => alternar(c)}>{rot}{chave === c ? <IconeSeta cima={!desc} /> : null}</button>
                </th>
              ))}
              <th scope="col">Predicado</th>
            </tr>
          </thead>
          <tbody>
            {linhas.map((x) => {
              const org = o({ nota: `nó ${x.node_id}` });
              return (
                <tr key={x.node_id}>
                  <td className="mono">{x.node_id}</td>
                  <td>{x.operador_fisico}{x.lookup ? <span className="sub"> (lookup)</span> : null}</td>
                  <td className="mono">{x.objeto ?? VAZIO}{x.tipo_indice ? <span className="sub"> · {x.tipo_indice}</span> : null}</td>
                  <td className="n"><Num valor={x.linhas_estimadas} origem={{ ...org, nota: `nó ${x.node_id}: EstimateRows` }} rotulo="linhas estimadas" /></td>
                  <td className="n"><Num valor={x.linhas_reais} origem={{ ...org, nota: `nó ${x.node_id}: ActualRows` }} rotulo="linhas reais" /></td>
                  <td className="n"><Num valor={x.execucoes} origem={{ ...org, nota: `nó ${x.node_id}: ActualExecutions` }} rotulo="execuções" /></td>
                  <td className="n"><Num valor={x.leituras_logicas} origem={{ ...org, nota: `nó ${x.node_id}: ActualLogicalReads` }} rotulo="leituras lógicas" /></td>
                  <td className="n"><Num valor={x.custo_subarvore} formato={(v) => fmtNum(v, 4)} origem={{ ...org, nota: `nó ${x.node_id}: EstimatedTotalSubtreeCost` }} rotulo="custo da subárvore" /></td>
                  <td className="n"><Num valor={x.razao_estimado_real} formato={fmtRazao} origem={{ ...org, nota: `nó ${x.node_id}: EstimateRows ÷ ActualRows` }} rotulo="estimado sobre real" /></td>
                  <td className="mono truncar" title={x.predicado ?? ''}>{x.predicado ?? VAZIO}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {plano.avisos?.length ? <Aviso tipo="alerta" titulo="Avisos do otimizador no plano"><ul>{plano.avisos.map((a) => <li key={a}>{a}</li>)}</ul></Aviso> : null}
      {plano.indices_ausentes?.length ? (
        <Aviso tipo="info" titulo="Índice sugerido pelo otimizador (MissingIndexes)">
          <ul>
            {plano.indices_ausentes.map((m, i) => (
              <li key={i}>
                <span className="mono">{m.tabela}</span>: igualdade ({(m.colunas.EQUALITY ?? []).join(', ') || 'n/d'}), desigualdade ({(m.colunas.INEQUALITY ?? []).join(', ') || 'n/d'}), inclui ({(m.colunas.INCLUDE ?? []).length ? `${m.colunas.INCLUDE.length} colunas` : 'n/d'}); impacto estimado <Num valor={m.impacto} formato={(v) => fmtPct(v, 1)} origem={o({ nota: 'MissingIndexGroup/@Impact' })} rotulo="impacto estimado" />. Informativo, não é autoridade.
              </li>
            ))}
          </ul>
        </Aviso>
      ) : null}
    </>
  );
}
