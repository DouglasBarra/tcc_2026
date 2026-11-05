import { useMemo, useState } from 'react';
import { useContexto } from '../contexto.jsx';
import { useApi } from '../hooks/useApi.js';
import { mensagemDeErro } from '../lib/api.js';
import { Aviso, Badge, Carregando, EstadoVazio } from '../componentes/EstadoVazio.jsx';
import { Num } from '../componentes/Num.jsx';
import { fmtData, fmtInt, VAZIO } from '../lib/formatar.js';

function Campo({ rotulo, valor, numero, origem, formato = fmtInt }) {
  return (
    <>
      <dt>{rotulo}</dt>
      <dd>{numero ? <Num valor={valor} formato={formato} origem={origem} rotulo={rotulo} /> : valor ?? VAZIO}</dd>
    </>
  );
}

export function diferencas(a, b) {
  if (!a || !b) return [];
  const campos = ['sql_version', 'edition', 'product_version', 'compat_level', 'ce_version', 'cpu_count', 'physical_memory_mb', 'max_server_memory_mb', 'maxdop', 'cost_threshold', 'container_platform', 'driver', 'python_version', 'tool_version', 'python_lock_hash'];
  return campos.filter((c) => JSON.stringify(a[c]) !== JSON.stringify(b[c]));
}

export default function Ambiente() {
  const { rodada } = useContexto();
  const r = useApi(rodada ? `/api/rodadas/${rodada}/ambiente` : null);
  const [idx, setIdx] = useState(0);
  const lista = r.dados?.ambientes ?? [];
  const amb = lista[Math.min(idx, lista.length - 1)] ?? null;
  const dif = useMemo(() => (lista.length > 1 ? diferencas(lista[0], lista[lista.length - 1]) : []), [lista]);
  const par = r.dados?.parametros_rodada;
  const o = (campo) => ({ rodada, env_id: amb?.env_id, tabela: 'environment', metrica: campo });

  return (
    <div className="pagina">
      <header>
        <div><h1>Ambiente</h1><p>Instantâneo da tabela <code>environment</code> gravado no início de cada experimento: sem ele, tempos de execução não são comparáveis.</p></div>
        <span className="sub">Rodada {rodada ?? VAZIO}</span>
      </header>
      {!rodada ? <EstadoVazio titulo="Nenhuma rodada selecionada" motivo="Sem tcclab.sqlite em pratica/experimento/resultados." />
        : r.carregando && !r.dados ? <Carregando />
          : r.erro ? <Aviso tipo="erro" titulo="Ambiente indisponível">{mensagemDeErro(r.erro)}</Aviso>
            : !amb ? <EstadoVazio titulo="Sem instantâneo de ambiente" motivo="A rodada não tem linhas em environment." />
              : (
                <>
                  {lista.length > 1 ? (
                    <div className="campo-linha">
                      <label className="campo"><span>Instantâneo</span>
                        <select name="selecao" value={idx} onChange={(e) => setIdx(Number(e.target.value))}>
                          {lista.map((x, i) => <option key={x.env_id} value={i}>ambiente {x.env_id} · {fmtData(x.captured_at_utc)} · experimentos {x.experimentos.join(', ') || 'n/d'}</option>)}
                        </select>
                      </label>
                      {dif.length ? <Aviso tipo="alerta" titulo="Ambientes diferentes na mesma rodada">Campos que diferem entre o primeiro e o último instantâneo: {dif.join(', ')}.</Aviso> : <Badge tipo="ok">primeiro e último instantâneo idênticos nos campos de configuração</Badge>}
                    </div>
                  ) : null}
                  {amb.avisos.length ? <Aviso tipo="alerta" titulo="Avisos registrados no início do experimento"><ul>{amb.avisos.map((a) => <li key={a}>{a}</li>)}</ul></Aviso> : null}
                  <div className="grade-2">
                    <section className="cartao" aria-labelledby="amb-srv">
                      <header><h2 id="amb-srv">Servidor</h2><span className="sub">ambiente {amb.env_id} · capturado em {fmtData(amb.captured_at_utc)}</span></header>
                      <dl className="kv corpo">
                        <Campo rotulo="Versão" valor={amb.sql_version} />
                        <Campo rotulo="Edição" valor={amb.edition} />
                        <Campo rotulo="Versão do produto" valor={amb.product_version} />
                        <Campo rotulo="Plataforma do contêiner" valor={amb.container_platform} />
                        <Campo rotulo="Emulação amd64 em arm64" valor={amb.emulacao_amd64_em_arm64 == null ? null : amb.emulacao_amd64_em_arm64 ? 'sim (tempos inflados e mais ruidosos)' : 'não'} />
                        <Campo rotulo="CPUs visíveis" numero valor={amb.cpu_count} origem={o('cpu_count')} />
                        <Campo rotulo="Memória física (MB)" numero valor={amb.physical_memory_mb} origem={o('physical_memory_mb')} />
                      </dl>
                    </section>
                    <section className="cartao" aria-labelledby="amb-cfg">
                      <header><h2 id="amb-cfg">Configuração que afeta o plano</h2></header>
                      <dl className="kv corpo">
                        <Campo rotulo="Nível de compatibilidade do banco" numero valor={amb.compat_level} origem={o('compat_level')} />
                        <Campo rotulo="Estimador de cardinalidade (modelo)" numero valor={amb.ce_version} origem={o('ce_version')} />
                        <Campo rotulo="MAXDOP da instância" numero valor={amb.maxdop} origem={o('maxdop')} />
                        <Campo rotulo="MAXDOP no escopo do banco (parâmetro da rodada)" numero valor={par?.maxdop} origem={{ rodada, tabela: 'rodada', metrica: 'maxdop' }} />
                        <Campo rotulo="MAXDOP efetivo medido no início do experimento" numero valor={amb.maxdop_efetivo} origem={o('bruto_json.maxdop_efetivo')} />
                        <Campo rotulo="Opções do escopo do banco (sys.database_scoped_configurations)" valor={amb.config_escopo_banco ? Object.entries(amb.config_escopo_banco).map(([k, v]) => `${k} = ${v}`).join(' · ') : 'não registrado neste instantâneo (captura anterior à leitura do escopo do banco)'} />
                        <Campo rotulo="Custo mínimo para paralelismo" numero valor={amb.cost_threshold} origem={o('cost_threshold')} />
                        <Campo rotulo="Memória máxima do SQL Server (MB)" numero valor={amb.max_server_memory_mb} origem={o('max_server_memory_mb')} />
                      </dl>
                      <p className="nota corpo">O MAXDOP efetivo das consultas é o do escopo do banco quando definido (diferente de 0); o da instância é só o padrão.</p>
                    </section>
                    <section className="cartao" aria-labelledby="amb-qs">
                      <header><h2 id="amb-qs">Query Store</h2></header>
                      {amb.query_store ? (
                        <dl className="kv corpo">
                          <Campo rotulo="Estado" valor={amb.query_store.estado} />
                          <Campo rotulo="Modo de captura" valor={amb.query_store.modo_captura} />
                          <Campo rotulo="Intervalo (min)" numero valor={amb.query_store.intervalo_min} origem={o('query_store_options_json[2]')} />
                          <Campo rotulo="Descarga (s)" numero valor={amb.query_store.flush_s} origem={o('query_store_options_json[3]')} />
                          <Campo rotulo="Armazenamento máximo (MB)" numero valor={amb.query_store.max_storage_mb} origem={o('query_store_options_json[4]')} />
                        </dl>
                      ) : <div className="corpo"><EstadoVazio titulo="Opções do Query Store não registradas" /></div>}
                    </section>
                    <section className="cartao" aria-labelledby="amb-tool">
                      <header><h2 id="amb-tool">Ferramenta e sessão de medição</h2></header>
                      <dl className="kv corpo">
                        <Campo rotulo="Banco medido" valor={amb.database} />
                        <Campo rotulo="Driver" valor={amb.driver} />
                        <Campo rotulo="tcclab" valor={amb.tool_version} />
                        <Campo rotulo="Commit" valor={amb.tool_commit ?? 'não registrado (a pasta não é repositório git)'} />
                        <Campo rotulo="Python" valor={amb.python_version} />
                        <Campo rotulo="Hash do requirements.lock" valor={amb.python_lock_hash} />
                      </dl>
                    </section>
                  </div>
                  <p className="nota">Experimentos que usam este instantâneo: {amb.experimentos.length ? amb.experimentos.join(', ') : VAZIO}.</p>
                </>
              )}
    </div>
  );
}
