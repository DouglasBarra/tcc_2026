import { useEffect, useMemo, useState } from 'react';
import { useApi } from '../../hooks/useApi.js';
import { enviar, mensagemDeErro, ApiError } from '../../lib/api.js';
import { Aviso, Badge, Carregando, EstadoVazio } from '../../componentes/EstadoVazio.jsx';
import PlanoOperadores from './PlanoOperadores.jsx';
import { VAZIO } from '../../lib/formatar.js';

/** Opcoes de fonte do plano: planos salvos em experimento/planos e planos gravados nas medicoes (um por plan_id). */
export function opcoesDePlano(detalhe, validacao) {
  const ops = [];
  const ordem = ['original.real', 'original.estimado', 'reescrita.real', 'reescrita.estimado'];
  const salvos = [...(detalhe?.planos_salvos ?? [])].sort((a, b) => ordem.indexOf(a.rotulo) - ordem.indexOf(b.rotulo));
  for (const p of salvos) ops.push({ id: `salvo:${p.rotulo}`, rotulo: `salvo · ${p.rotulo}`, tipo: 'salvo', rotuloPlano: p.rotulo });
  const vistos = new Set();
  for (const r of validacao?.runs ?? []) {
    if (r.plan_id == null || vistos.has(r.plan_id)) continue;
    vistos.add(r.plan_id);
    ops.push({ id: `plano:${r.plan_id}`, rotulo: `medição · plano ${r.plan_id} (${r.variante}, ${r.regime}, run ${r.run_id})`, tipo: 'plano', planId: r.plan_id, runId: r.run_id });
  }
  return ops;
}

export function usePlano(id, rodada, opcao) {
  const url = !opcao ? null : opcao.tipo === 'salvo' ? `/api/consultas/${id}/planos-salvos/${opcao.rotuloPlano}` : `/api/rodadas/${rodada}/planos/${opcao.planId}`;
  return useApi(url);
}

export function origemPlano(opcao, rodada, dadosPlano) {
  if (!opcao) return {};
  if (opcao.tipo === 'salvo') return { nota: dadosPlano?.origem ?? `experimento/planos · ${opcao.rotuloPlano}` };
  return { rodada, plan_id: opcao.planId, run_ids: [opcao.runId], tabela: 'plan' };
}

function ResultadoGenerico({ r }) {
  if (r == null) return null;
  return <pre className="codigo">{JSON.stringify(r, null, 2)}</pre>;
}

const TIPO_GRAV = { alta: 'erro', media: 'alerta', baixa: '' };

/** Renderiza o objeto Diagnostico de tcclab.diagnostico (achados, regras executadas/ignoradas, avisos, limiares). */
export function ResultadoDiagnostico({ r, declarado }) {
  if (!r || !Array.isArray(r.achados)) return <ResultadoGenerico r={r} />;
  const detectados = [...new Set(r.achados.map((a) => a.antipadrao).filter(Boolean))].sort();
  const ignoradas = Object.entries(r.regras_ignoradas ?? {});
  return (
    <>
      <div className="chips">
        <Badge tipo={r.plano === 'nenhum' || r.plano === 'invalido' ? 'alerta' : 'info'}>plano usado: {r.plano}</Badge>
        <Badge tipo={r.sql_analisavel ? 'ok' : 'erro'}>SQL {r.sql_analisavel ? 'analisável' : 'NÃO analisável'}</Badge>
        <Badge>regras executadas: {(r.regras_executadas ?? []).join(', ') || VAZIO}</Badge>
        {declarado ? <Badge tipo={detectados.includes(declarado) ? 'ok' : 'alerta'}>declarado {declarado} · detectado {detectados.join(', ') || 'nenhum'}</Badge> : null}
      </div>
      {!r.sql_analisavel ? <Aviso tipo="erro" titulo="SQL não analisável">{r.motivo_sql}. Isto nunca significa “sem problemas”.</Aviso> : null}
      {ignoradas.length ? <Aviso tipo="alerta" titulo="Regras NÃO executadas"><ul>{ignoradas.map(([k, v]) => <li key={k}><span className="mono">{k}</span>: {v}</li>)}</ul></Aviso> : null}
      {(r.avisos ?? []).length ? <Aviso tipo="info" titulo="Avisos do módulo"><ul>{r.avisos.map((a) => <li key={a}>{a}</li>)}</ul></Aviso> : null}
      {r.achados.length === 0 ? (
        <EstadoVazio titulo="Nenhum achado nas regras executadas" motivo="Isto não prova ausência de problemas nas regras ignoradas acima." />
      ) : (
        <div className="rolagem">
          <table className="tabela">
            <caption>Achados (ordenados por gravidade e nível). “Confirmado” = SQL e plano concordam; “indício” = só uma das fontes.</caption>
            <thead><tr><th scope="col">Id</th><th scope="col">Antipadrão</th><th scope="col">Gravidade</th><th scope="col">Nível</th><th scope="col">Fontes</th><th scope="col">Evidência</th><th scope="col">Explicação</th></tr></thead>
            <tbody>
              {r.achados.map((a, i) => {
                const anterior = r.achados.slice(0, i).find((b) => b.explicacao && b.explicacao === a.explicacao);
                return (
                <tr key={a.id}>
                  <th scope="row" className="mono nowrap">{a.id}</th>
                  <td className="mono">{a.antipadrao ?? VAZIO}</td>
                  <td><Badge tipo={TIPO_GRAV[a.gravidade] ?? ''}>{a.gravidade}</Badge></td>
                  <td><Badge tipo={a.nivel === 'confirmado' ? 'ok' : 'alerta'}>{a.nivel}</Badge>{a.corrobora?.length ? <span className="sub"> ⇄ {a.corrobora.join(', ')}</span> : null}</td>
                  <td>{(a.fontes ?? []).join(' + ')}</td>
                  <td>{a.evidencia}{a.linha_sql ? <span className="sub"> (linha {a.linha_sql} do SQL)</span> : null}{a.no_plano != null ? <span className="sub"> (nó {a.no_plano} do plano)</span> : null}</td>
                  <td>{anterior ? <span className="sub">mesma explicação de {anterior.id}</span> : <>{a.explicacao}{(a.notas ?? []).map((n) => <div key={n} className="sub">nota: {n}</div>)}</>}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <details>
        <summary className="sub">Limiares usados pelas regras (suposições a calibrar, segundo o módulo)</summary>
        <dl className="kv">{Object.entries(r.limiares ?? {}).map(([k, v]) => (<><dt key={`k${k}`} className="mono">{k}</dt><dd key={`v${k}`} className="mono">{String(v)}</dd></>))}</dl>
      </details>
    </>
  );
}

export function PainelDiagnostico({ modulo, resultado, declarado }) {
  if (!modulo?.disponivel) {
    return (
      <EstadoVazio titulo="Módulo de diagnóstico ainda não disponível" motivo={`${modulo?.motivo ?? ''}. A interface não aplica regras de antipadrão por conta própria; quando tcclab.diagnostico existir, as evidências aparecem aqui.`} />
    );
  }
  if (!resultado) return <p className="nota">Módulo disponível. Execute o diagnóstico para ver as evidências.</p>;
  if (resultado.erro) return <Aviso tipo="erro" titulo="O módulo de diagnóstico falhou">{resultado.erro}</Aviso>;
  return <ResultadoDiagnostico r={resultado.resultado} declarado={declarado} />;
}

export default function Diagnostico({ id, rodada, detalhe, validacao, estado, planoSel, setPlanoSel, textoEditor, textoCatalogo, onAnalisado }) {
  const opcoes = useMemo(() => opcoesDePlano(detalhe, validacao.dados), [detalhe, validacao.dados]);
  const sel = opcoes.find((o) => o.id === planoSel) ?? opcoes[0] ?? null;
  const plano = usePlano(id, rodada, sel);
  const modulo = estado.dados?.modulos?.diagnostico;
  const ao = estado.dados?.ao_vivo;

  const salvos = useMemo(() => (detalhe?.planos_salvos ?? []).filter((p) => p.rotulo.startsWith('original')), [detalhe]);
  const padraoDiag = salvos.find((p) => p.rotulo === 'original.real')?.rotulo ?? salvos.find((p) => p.rotulo === 'original.estimado')?.rotulo ?? '';
  const [rotuloDiag, setRotuloDiag] = useState(null);
  const planoDiag = rotuloDiag ?? padraoDiag;
  const [off, setOff] = useState({ carregando: false, dados: null });
  const [vivo, setVivo] = useState({ carregando: false, erro: null, dados: null });
  useEffect(() => { setRotuloDiag(null); setOff({ carregando: false, dados: null }); setVivo({ carregando: false, erro: null, dados: null }); }, [id]);

  // diagnostico offline (sem SQL Server): roda sozinho quando o modulo existe, sobre o SQL do catalogo e o plano salvo
  const moduloOk = Boolean(modulo?.disponivel);
  useEffect(() => {
    if (!moduloOk) return undefined;
    let vivoEfeito = true;
    setOff({ carregando: true, dados: null });
    enviar(`/api/consultas/${id}/diagnostico`, { rotulo_plano: planoDiag || null })
      .then((d) => { if (vivoEfeito) { setOff({ carregando: false, dados: d }); onAnalisado?.({ texto: textoCatalogo, achados: d.diagnostico?.resultado?.achados ?? [] }); } })
      .catch((e) => { if (vivoEfeito) setOff({ carregando: false, dados: { diagnostico: { disponivel: true, erro: mensagemDeErro(e), resultado: null } } }); });
    return () => { vivoEfeito = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, moduloOk, planoDiag]);

  const rodarVivo = async () => {
    setVivo({ carregando: true, erro: null, dados: null });
    try {
      const d = await enviar('/api/ao-vivo/diagnosticar', { sql: textoEditor });
      setVivo({ carregando: false, erro: null, dados: d });
      onAnalisado?.({ texto: textoEditor, achados: d.diagnostico?.resultado?.achados ?? [] });
    } catch (e) {
      setVivo({ carregando: false, erro: e instanceof ApiError && e.status === 409 ? `Recusado (HTTP 409): ${e.detalhe}` : mensagemDeErro(e), dados: null });
    }
  };

  return (
    <>
      <div className="camada"><span className="numero" aria-hidden="true">1</span><h2>Diagnóstico: evidências objetivas</h2></div>
      <p className="nota">Evidências vêm de duas fontes que devem se confirmar: análise estática do texto (tcclab.diagnostico) e o plano de execução. A interface mostra os campos do plano como estão no XML; não os interpreta.</p>

      <section className="cartao" aria-labelledby="d-det">
        <header><h3 id="d-det">Detecção de antipadrões (tcclab.diagnostico)</h3>
          {moduloOk ? (
            <label className="campo"><span className="so-leitor">Plano usado no diagnóstico</span>
              <select name="selecao" aria-label="Plano usado no diagnóstico" value={planoDiag} onChange={(e) => setRotuloDiag(e.target.value)}>
                <option value="">nenhum (só regras de SQL)</option>
                {salvos.map((p) => <option key={p.rotulo} value={p.rotulo}>{p.rotulo}</option>)}
              </select>
            </label>
          ) : null}
        </header>
        <div className="corpo">
          {off.carregando ? <Carregando texto="Diagnosticando o SQL do catálogo…" /> : null}
          {!off.carregando ? <PainelDiagnostico modulo={modulo} resultado={off.dados?.diagnostico} declarado={detalhe.antipadrao} /> : null}
          <p className="nota">
            Offline: o SQL do catálogo e o plano salvo em disco; nada vai ao SQL Server. Classe declarada no catálogo (não é detecção): <Badge tipo="acento">{detalhe.antipadrao}</Badge> {detalhe.descricao_antipadrao ?? ''}.
            {moduloOk && off.dados?.diagnostico?.resultado ? ' Linhas citadas pelos achados aparecem destacadas no editor.' : ''}
          </p>
        </div>
      </section>

      <section className="cartao" aria-labelledby="d-plano">
        <header>
          <h3 id="d-plano">Plano de execução</h3>
          {opcoes.length ? (
            <label className="campo"><span className="so-leitor">Fonte do plano</span>
              <select name="selecao" value={sel?.id ?? ''} onChange={(e) => setPlanoSel(e.target.value)} aria-label="Fonte do plano">
                {opcoes.map((o) => <option key={o.id} value={o.id}>{o.rotulo}</option>)}
              </select>
            </label>
          ) : null}
        </header>
        <div className="corpo">
          {!opcoes.length ? (
            <EstadoVazio titulo="Nenhum plano disponível para esta consulta" motivo="Sem planos salvos (rode experimento/salvar_planos.py) e sem plano gravado em medição na rodada selecionada." />
          ) : plano.carregando ? <Carregando texto="Carregando plano…" />
            : plano.erro ? <Aviso tipo="erro" titulo="Plano indisponível">{mensagemDeErro(plano.erro)}</Aviso>
              : plano.dados?.motivo_sem_xml ? <EstadoVazio titulo="Arquivo do plano ausente" motivo={plano.dados.motivo_sem_xml} />
                : <PlanoOperadores plano={plano.dados?.resumo} origem={origemPlano(sel, rodada, plano.dados)} />}
        </div>
      </section>

      <section className="cartao" aria-labelledby="d-vivo">
        <header><h3 id="d-vivo">Diagnosticar o texto do editor (ao vivo)</h3>
          <button type="button" className="btn" onClick={rodarVivo} disabled={!ao?.habilitado || vivo.carregando || ao?.trava?.ocupada || Boolean(ao?.coletas_em_andamento?.length)}>Diagnosticar ao vivo</button>
        </header>
        <div className="corpo">
          {!ao?.habilitado ? (
            <Aviso tipo="info" titulo="Modo ao vivo desligado">{ao?.motivo_desabilitado ?? 'Estado do modo ao vivo desconhecido.'} O botão envia o texto do editor ao backend, que pede o plano <em>estimado</em> (a consulta não é executada).</Aviso>
          ) : null}
          {vivo.carregando ? <Carregando texto="Pedindo o plano estimado…" /> : null}
          {vivo.erro ? <Aviso tipo="erro" titulo="Falha no diagnóstico ao vivo">{vivo.erro}</Aviso> : null}
          {vivo.dados ? (
            <>
              <p className="nota">Banco {vivo.dados.banco} · plano {vivo.dados.tipo_plano} capturado em {vivo.dados.capturado_utc} UTC.</p>
              <PainelDiagnostico modulo={vivo.dados.diagnostico} resultado={vivo.dados.diagnostico.disponivel ? vivo.dados.diagnostico : null} />
              <PlanoOperadores plano={vivo.dados.plano} origem={{ nota: `captura ao vivo no banco ${vivo.dados.banco}` }} />
            </>
          ) : null}
        </div>
      </section>
    </>
  );
}
