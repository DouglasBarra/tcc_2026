import { Num } from '../../componentes/Num.jsx';
import { Aviso, EstadoVazio } from '../../componentes/EstadoVazio.jsx';
import { fmtBytes, fmtIC, fmtInt, fmtNum, fmtRazao, rotuloMetrica, rotuloOperacao } from '../../lib/formatar.js';

const COLUNAS = ['logical_reads', 'log_bytes', 'cpu_ms'];

export default function CustoEscrita({ escrita, rodada }) {
  if (escrita.carregando) return <p className="carregando" role="status">Carregando custo de escrita…</p>;
  if (escrita.erro) return <Aviso tipo="erro" titulo="Custo de escrita indisponível">{escrita.erro.detalhe ?? 'Falha ao ler.'}</Aviso>;
  const e = escrita.dados;
  if (!e?.disponivel) return <EstadoVazio titulo="Sem custo de escrita medido" motivo={e?.motivo} />;
  const ind = e.indice;
  const orig = (op, m, lado, linhas) => ({ rodada, tabela: 'escrita_medicao', metrica: m, linhas, nota: `${rotuloOperacao(op)} de ${ind.k} linhas ${lado} índice, em transação revertida` });
  return (
    <div className="rolagem">
      <table className="tabela">
        <caption>
          Índice <span className="mono">{ind.nome}</span> em <span className="mono">{ind.tabela}</span>: <Num valor={ind.paginas} origem={{ rodada, tabela: 'escrita_indice', metrica: 'paginas', nota: 'páginas usadas pelo índice depois de criado' }} rotulo="páginas" /> páginas, criado em <Num valor={ind.criacao_ms} formato={(x) => `${fmtNum(x, 1)} ms`} origem={{ rodada, tabela: 'escrita_indice', metrica: 'criacao_ms' }} rotulo="tempo de criação" />. Mediana de cada operação sem e com o índice; razão com/sem.
          {e.ratio_disponivel ? '' : ` Razão e IC indisponíveis: ${e.motivo_razao}.`}
        </caption>
        <thead>
          <tr>
            <th scope="col">Operação</th>
            {COLUNAS.map((m) => <th key={m} scope="col" colSpan={1}>{rotuloMetrica(m)}: sem → com (razão; IC 95 %)</th>)}
          </tr>
        </thead>
        <tbody>
          {e.operacoes.map((o) => (
            <tr key={o.operacao}>
              <th scope="row">{rotuloOperacao(o.operacao)}</th>
              {COLUNAS.map((m) => {
                const x = o.metricas[m];
                const f = m === 'log_bytes' ? fmtBytes : fmtInt;
                return (
                  <td key={m}>
                    <Num valor={x.sem_indice?.mediana} formato={f} origem={orig(o.operacao, m, 'sem', x.linhas_sem_indice)} rotulo={`${m} sem índice`} />
                    {' → '}
                    <Num valor={x.com_indice?.mediana} formato={f} origem={orig(o.operacao, m, 'com', x.linhas_com_indice)} rotulo={`${m} com índice`} />
                    <div className="sub">
                      razão <Num valor={x.razao ?? x.razao_reg} formato={fmtRazao} origem={orig(o.operacao, m, 'com/sem', [...x.linhas_sem_indice, ...x.linhas_com_indice])} rotulo="razão" />
                      {' '}{fmtIC(x.ic_lo, x.ic_hi)}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="nota">Razão por operação, sem limiar de aceitação (conforme PRE-REGISTRO.md, que retirou o critério de 10 % antes da coleta).</p>
    </div>
  );
}
