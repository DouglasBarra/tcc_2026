import { useEffect, useRef, useState } from 'react';
import { Aviso, Badge } from '../../componentes/EstadoVazio.jsx';
import { enviar, mensagemDeErro, obter, ApiError } from '../../lib/api.js';
import { IconeRaio } from '../../componentes/Icones.jsx';
import { useContexto } from '../../contexto.jsx';

/**
 * Validacao ao vivo (antes x depois): so envia ao backend quando o modo ao vivo esta habilitado.
 * "Antes" = variante original do editor; "Depois" = variante selecionada (SQL e DDL do indice).
 */
export default function ValidacaoAoVivo({ estado, antes, depois, ddl, queryId, tabelas, onConcluir }) {
  const { recarregar } = useContexto();
  const ao = estado.dados?.ao_vivo;
  const [regimes, setRegimes] = useState({ quente: true, frio: false });
  const [reps, setReps] = useState({ quente: 10, frio: 5 });
  const [aquec, setAquec] = useState(3);
  const [job, setJob] = useState(null);
  const [erro, setErro] = useState(null);
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  const habilitado = Boolean(ao?.habilitado);
  const bloqueio = ao?.coletas_em_andamento?.length ? `há coleta em andamento (${ao.coletas_em_andamento.map((c) => c.rodada).join(', ')})` : ao?.trava?.ocupada ? `trava ocupada: ${ao.trava.dono}` : null;
  const regimesSel = Object.entries(regimes).filter(([, v]) => v).map(([k]) => k);
  const executando = job?.estado === 'executando';
  const semMudanca = (!depois || depois === antes) && !ddl;

  const acompanhar = (id) => {
    const passo = async () => {
      try {
        const j = await obter(`/api/ao-vivo/trabalhos/${id}`);
        setJob(j);
        if (j.estado === 'executando') timer.current = setTimeout(passo, 1000);
        else { recarregar(); if (j.estado === 'concluido') onConcluir?.(j); }   // recarrega rodadas e estado (a rodada ao vivo acabou de nascer)
      } catch (e) {
        setErro(mensagemDeErro(e));
      }
    };
    passo();
  };

  const iniciar = async () => {
    setErro(null);
    try {
      const corpo = {
        sql_antes: antes, sql_depois: depois && depois !== antes ? depois : null, ddl_indice: ddl || null,
        query_id: queryId, tabelas, regimes: regimesSel, repeticoes: reps, aquecimentos: aquec, titulo: `ao vivo: ${queryId}`,
      };
      const j = await enviar('/api/ao-vivo/validar', corpo);
      setJob(j);
      acompanhar(j.id);
    } catch (e) {
      setErro(e instanceof ApiError && e.status === 409 ? `Recusado (HTTP 409): ${e.detalhe}` : mensagemDeErro(e));
    }
  };

  return (
    <details className="cartao">
      <summary style={{ padding: '8px 12px', cursor: 'pointer', fontWeight: 600 }}>
        <IconeRaio /> Validar ao vivo (antes × depois) <Badge tipo={habilitado ? 'info' : ''}>{habilitado ? 'habilitado' : 'desligado'}</Badge>
      </summary>
      <div className="corpo">
        {!habilitado ? (
          <Aviso tipo="info" titulo="Modo ao vivo desligado">
            {ao?.motivo_desabilitado ?? 'O backend não informou o estado do modo ao vivo.'} Os números desta aba vêm somente de SQLite já gravado.
          </Aviso>
        ) : (
          <>
            <p className="nota">Executa o protocolo do núcleo (ABA, aquecimento, regimes) em um SQLite próprio da validação ao vivo, atrás da trava global (um experimento por vez). Mede <strong>{antes ? 'o texto original do editor' : 'n/d'}</strong> contra <strong>a variante selecionada</strong>.</p>
            <div className="campo-linha">
              <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
                <legend className="sub">Regimes</legend>
                {['quente', 'frio'].map((r) => (
                  <label key={r} style={{ marginRight: 12 }}><input type="checkbox" name="opcao" checked={regimes[r]} onChange={(e) => setRegimes((x) => ({ ...x, [r]: e.target.checked }))} /> {r}</label>
                ))}
              </fieldset>
              <label className="campo"><span>Repetições (quente)</span><input type="number" name="numero" min="1" max="60" value={reps.quente} onChange={(e) => setReps((x) => ({ ...x, quente: Number(e.target.value) }))} style={{ width: 90 }} /></label>
              <label className="campo"><span>Repetições (frio)</span><input type="number" name="numero" min="1" max="60" value={reps.frio} onChange={(e) => setReps((x) => ({ ...x, frio: Number(e.target.value) }))} style={{ width: 90 }} /></label>
              <label className="campo"><span>Aquecimentos</span><input type="number" name="numero" min="0" max="5" value={aquec} onChange={(e) => setAquec(Number(e.target.value))} style={{ width: 90 }} /></label>
              <button type="button" className="btn primario" onClick={iniciar} disabled={executando || !regimesSel.length || Boolean(bloqueio) || semMudanca}>Validar ao vivo</button>
            </div>
            {semMudanca ? <Aviso tipo="info" titulo="Nada a comparar">A variante selecionada no editor é idêntica à original e não aplica índice. Escolha reescrita, índice ou reescrita+índice (ou edite o texto) para definir o “depois”.</Aviso> : null}
            {bloqueio ? <Aviso tipo="alerta" titulo="Bloqueado">{bloqueio}. A API responderia HTTP 409.</Aviso> : null}
          </>
        )}
        {erro ? <Aviso tipo="erro" titulo="Falha">{erro}</Aviso> : null}
        {job ? (
          <div aria-live="polite">
            <p><strong>Trabalho {job.id}</strong> <Badge tipo={job.estado === 'concluido' ? 'ok' : job.estado === 'executando' ? 'info' : 'erro'}>{job.estado}</Badge>{job.erro ? ` ${job.erro}` : ''}</p>
            <pre className="log">{job.log.join('\n') || '…'}</pre>
          </div>
        ) : null}
      </div>
    </details>
  );
}
