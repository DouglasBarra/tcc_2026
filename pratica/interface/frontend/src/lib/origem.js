// Monta o texto de rastreabilidade ("de onde veio este numero") usado em tooltips e colunas "Origem".
import { fmtLista } from './formatar.js';

/**
 * origem: { rodada, experiment_id, run_ids, metrica, n, fonte, tabela, plan_id, env_id, linhas, nota }
 * Qualquer campo ausente e omitido; nunca se inventa um valor.
 */
export function descreverOrigem(o) {
  if (!o) return '';
  const p = [];
  if (o.rodada) p.push(`rodada ${o.rodada}`);
  if (o.experiment_id != null) p.push(`experimento ${o.experiment_id}`);
  if (o.run_ids?.length) p.push(`${o.run_ids.length === 1 ? 'run' : 'runs'} ${fmtLista(o.run_ids)}`);
  if (o.plan_id != null) p.push(`plano ${o.plan_id}`);
  if (o.env_id != null) p.push(`ambiente ${o.env_id}`);
  if (o.tabela && o.metrica) p.push(`${o.tabela}.${o.metrica}`);
  else if (o.tabela) p.push(o.tabela);
  else if (o.metrica) p.push(o.metrica);
  if (o.linhas?.length) p.push(`${o.linhas.length === 1 ? 'linha' : 'linhas'} ${resumirIntervalo(o.linhas)} de escrita_medicao`);
  if (o.n != null) p.push(`n=${o.n}`);
  if (o.fonte) p.push(o.fonte);
  if (o.nota) p.push(o.nota);
  return p.join(' · ');
}

function resumirIntervalo(ids) {
  const xs = [...ids].sort((a, b) => a - b);
  return xs.length > 3 ? `${xs[0]}–${xs[xs.length - 1]}` : xs.join(', ');
}

/** Versao curta para a coluna "Origem": "exp 1 · runs 1, 5". */
export function origemCurta(o) {
  if (!o) return '';
  const p = [];
  if (o.experiment_id != null) p.push(`exp ${o.experiment_id}`);
  if (o.run_ids?.length) p.push(`${o.run_ids.length === 1 ? 'run' : 'runs'} ${fmtLista(o.run_ids)}`);
  if (o.linhas?.length) p.push(`escrita ${resumirIntervalo(o.linhas)}`);
  if (o.plan_id != null) p.push(`plano ${o.plan_id}`);
  if (o.env_id != null) p.push(`amb ${o.env_id}`);
  return p.join(' · ');
}
