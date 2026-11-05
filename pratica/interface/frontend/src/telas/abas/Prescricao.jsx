import { Num } from '../../componentes/Num.jsx';
import { Aviso, Badge, EstadoVazio } from '../../componentes/EstadoVazio.jsx';
import { fmtNum, VAZIO } from '../../lib/formatar.js';

function Codigo({ rotulo, texto }) {
  return (
    <div className="codigo">
      <span className="rotulo">{rotulo}</span>
      <pre>{texto ?? VAZIO}</pre>
    </div>
  );
}

/** Por que nao ha braco nativo: usa SOMENTE o que a coleta registrou (notas de coleta_consulta e de catalogo.variantes_de). */
export function motivoSemNativo(detalhe, validacao) {
  if (!validacao?.disponivel) return 'Sem coleta desta consulta na rodada selecionada: não há registro do índice sugerido pelo otimizador.';
  const reg = validacao.registro_coleta ?? [];
  const ultima = [...reg].reverse().find((r) => r.experiment_id === validacao.experimento?.experiment_id) ?? reg[reg.length - 1];
  const notas = [...(ultima?.notas ?? []), ...(detalhe.notas_variantes ?? [])];
  const achada = notas.find((n) => /nao sugeriu|não sugeriu/i.test(n));
  const igual = notas.find((n) => /nativo igual/i.test(n));
  if (achada) return 'O otimizador não sugeriu índice para a consulta original (nota gravada pela coleta).';
  if (igual) return 'O índice sugerido pelo otimizador é igual ao do framework: o braço não é medido separadamente (nota gravada pela coleta).';
  if (!detalhe.tem_indice) return 'A consulta não tem índice a prescrever, então a coleta não captura o braço nativo.';
  return 'A coleta não registrou índice sugerido pelo otimizador para esta consulta.';
}

export default function Prescricao({ detalhe, validacao, escrita, rodada }) {
  const planej = detalhe.variantes_planejadas ?? [];
  const nativo = planej.find((v) => v.kind === 'nativo');
  const presc = planej.find((v) => v.kind === 'prescricao') ?? planej.find((v) => v.kind === 'indice');
  const eq = validacao.dados?.coleta?.equivalencia;
  const ind = escrita.dados?.disponivel ? escrita.dados.indice : null;
  const extras = planej.filter((v) => !['original', 'reescrita', 'indice', 'prescricao', 'nativo'].includes(v.kind));

  return (
    <>
      <div className="camada"><span className="numero" aria-hidden="true">3</span><h2>Prescrição: reescrita e índice</h2></div>
      <Aviso tipo="info" titulo="Prescrição declarada no catálogo">
        Os textos abaixo foram escritos pelo autor em {detalhe.arquivo}. A geração automática (reescrita segura e ordem das colunas do índice) ainda não existe no núcleo; quando existir, aparece nesta aba com sua justificativa.
      </Aviso>

      {detalhe.sql_reescrita ? (
        <section className="cartao" aria-labelledby="p-rew">
          <header><h3 id="p-rew">A. Reescrita da consulta</h3>
            {eq ? <Badge tipo={eq.ok ? 'ok' : 'erro'}>equivalência de resultado: {eq.ok ? 'idêntico' : 'divergente'}</Badge> : <Badge>equivalência: sem registro nesta rodada</Badge>}
          </header>
          <div className="corpo grade-2">
            <Codigo rotulo="Antes (original)" texto={detalhe.sql_original} />
            <Codigo rotulo="Depois (reescrita)" texto={detalhe.sql_reescrita} />
          </div>
          {detalhe.sql_equivalencia ? <div className="corpo"><Codigo rotulo="Consulta usada para verificar a equivalência (a reescrita muda a projeção)" texto={detalhe.sql_equivalencia} /></div> : null}
          {eq?.aplicavel ? (
            <div className="corpo nota">
              Equivalência verificada por hash de multiconjunto das linhas, antes de medir: <Num valor={eq.linhas} origem={{ rodada, tabela: 'coleta_consulta', metrica: 'equivalencia_json.linhas', experiment_id: validacao.dados?.experimento?.experiment_id }} rotulo="linhas do resultado" /> linhas.
            </div>
          ) : null}
        </section>
      ) : (
        <EstadoVazio titulo="Esta consulta não tem reescrita no catálogo" motivo="Classe AP4 (varredura por falta de índice) é tratada só com índice." />
      )}

      <section className="cartao" aria-labelledby="p-idx">
        <header><h3 id="p-idx">B. Índice</h3></header>
        <div className="corpo" style={{ display: 'grid', gap: 12 }}>
          {presc?.ddl_aplicar ? (
            <>
              <Codigo rotulo={`Criar (variante ${presc.nome})`} texto={presc.ddl_aplicar} />
              <Codigo rotulo="Reverter" texto={presc.ddl_reverter} />
            </>
          ) : <EstadoVazio titulo="Sem índice declarado" motivo="O catálogo não define índice para esta consulta." />}
          {ind ? (
            <p className="nota">
              Medido na coleta: <Num valor={ind.paginas} origem={{ rodada, tabela: 'escrita_indice', metrica: 'paginas', nota: `índice ${ind.nome}` }} rotulo="páginas do índice" /> páginas usadas; criação em <Num valor={ind.criacao_ms} formato={(x) => `${fmtNum(x, 1)} ms`} origem={{ rodada, tabela: 'escrita_indice', metrica: 'criacao_ms', nota: `índice ${ind.nome}` }} rotulo="tempo de criação" />. Custo de escrita completo na aba Validação.
            </p>
          ) : <p className="nota">Tamanho e custo de escrita do índice: sem medição nesta rodada.</p>}
          <p className="nota">Justificativa da ordem das colunas: não gerada (marco M3). A ordem acima é a declarada pelo autor.</p>
        </div>
      </section>

      <section className="cartao" aria-labelledby="p-nat">
        <header><h3 id="p-nat">C. Índice sugerido pelo otimizador (braço de comparação)</h3></header>
        <div className="corpo" style={{ display: 'grid', gap: 12 }}>
          {nativo ? <Codigo rotulo="Capturado do plano estimado (MissingIndexes) na coleta" texto={nativo.ddl_aplicar} />
            : <EstadoVazio titulo="Sem braço nativo nesta rodada" motivo={motivoSemNativo(detalhe, validacao.dados)} />}
        </div>
      </section>

      {extras.length ? (
        <section className="cartao" aria-labelledby="p-ext">
          <header><h3 id="p-ext">D. Braços extras (contraexemplos e controles)</h3></header>
          <div className="corpo" style={{ display: 'grid', gap: 12 }}>
            {extras.map((v) => <Codigo key={v.nome} rotulo={`${v.nome} (${v.kind})`} texto={v.ddl_aplicar} />)}
          </div>
        </section>
      ) : null}
    </>
  );
}
