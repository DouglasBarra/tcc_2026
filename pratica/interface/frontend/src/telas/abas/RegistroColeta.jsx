import { Badge, EstadoVazio } from '../../componentes/EstadoVazio.jsx';
import { Num } from '../../componentes/Num.jsx';
import { fmtNum, fmtPct, VAZIO } from '../../lib/formatar.js';

const ROTULO_STATUS = {
  concluido: ['concluído', 'ok'],
  invalida_equivalencia: ['invalidada: resultado diferente', 'erro'],
  erro: ['erro na coleta', 'erro'],
};

/**
 * Tudo o que a coleta registrou para a consulta: tentativas, descartes por deriva, invalidacao por equivalencia,
 * braco nativo ausente e erros. E o que explica por que um numero pode nao existir.
 */
export default function RegistroColeta({ validacao, rodada }) {
  const reg = validacao.registro_coleta ?? [];
  if (!reg.length) {
    const andamento = validacao.experimento?.status === 'executando';
    return (
      <EstadoVazio
        titulo="Sem registro da coleta (coleta_consulta)"
        motivo={andamento
          ? 'O coletor ainda está executando este experimento: ele grava o registro (tentativa, deriva, equivalência, braço nativo) ao terminar a consulta.'
          : 'Esta rodada não tem a tabela auxiliar de coleta, ou a consulta não passou pelo coletor (por exemplo, validação ao vivo).'}
      />
    );
  }
  return (
    <div className="rolagem">
      <table className="tabela">
        <caption>Cada linha é uma tentativa registrada pelo coletor (coleta_consulta). Tentativas descartadas por deriva continuam gravadas.</caption>
        <thead>
          <tr><th scope="col">Tentativa</th><th scope="col">Experimento</th><th scope="col">Status</th><th scope="col" className="n">Deriva A1→A2 (CPU)</th><th scope="col">Descartada por deriva</th><th scope="col">Equivalência de resultado</th><th scope="col">Braço nativo</th><th scope="col" className="n">Duração</th><th scope="col">Notas e erro</th></tr>
        </thead>
        <tbody>
          {reg.map((r) => {
            const [rot, tipo] = ROTULO_STATUS[r.status] ?? [r.status, 'alerta'];
            const o = (m) => ({ rodada, experiment_id: r.experiment_id ?? undefined, tabela: 'coleta_consulta', metrica: m, nota: `linha ${r.linha}` });
            const eq = r.equivalencia;
            return (
              <tr key={r.linha}>
                <th scope="row" className="n mono">{r.tentativa ?? VAZIO}</th>
                <td className="mono">{r.experiment_id ?? 'nenhum'}</td>
                <td><Badge tipo={tipo}>{rot}</Badge></td>
                <td className="n"><Num valor={r.deriva_a1_a2 == null ? null : r.deriva_a1_a2 * 100} formato={(v) => fmtPct(v, 1)} origem={o('deriva_a1_a2')} rotulo="deriva A1 para A2" /></td>
                <td>{r.descartado_por_deriva ? <Badge tipo="alerta">sim: substituída por nova tentativa</Badge> : r.descartado_por_deriva === false ? 'não' : VAZIO}</td>
                <td>{eq ? (eq.aplicavel === false ? 'sem reescrita' : <Badge tipo={eq.ok ? 'ok' : 'erro'}>{eq.ok ? 'idêntico' : 'divergente: não medida'}{eq.linhas != null ? <> · <Num valor={eq.linhas} origem={o('equivalencia_json.linhas')} rotulo="linhas" /> linhas</> : null}</Badge>) : VAZIO}</td>
                <td>{r.nativo_ddl ? <Badge tipo="ok" titulo={r.nativo_ddl}>sugerido pelo otimizador</Badge> : <Badge titulo="coleta_consulta.nativo_ddl vazio">otimizador não sugeriu índice</Badge>}</td>
                <td className="n"><Num valor={r.duracao_s} formato={(v) => `${fmtNum(v, 0)} s`} origem={o('duracao_s')} rotulo="duração" /></td>
                <td className="sub">{[...(r.notas ?? []), r.erro].filter(Boolean).join(' · ') || VAZIO}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
