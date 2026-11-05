import { useMemo, useState } from 'react';
import { useContexto } from '../contexto.jsx';
import { Aviso, Badge, Carregando, EstadoVazio } from '../componentes/EstadoVazio.jsx';
import { Num } from '../componentes/Num.jsx';
import { montarHash } from '../lib/roteador.js';
import { mensagemDeErro } from '../lib/api.js';
import { rotuloPapel, rotuloSeletividade } from '../lib/formatar.js';

const SELETIVIDADES = ['alta', 'media', 'baixa'];

export function statusNaRodada(consulta, rodada) {
  const m = consulta.medida_em?.find((x) => x.rodada === rodada);
  if (!m) return { tipo: 'nao', rotulo: 'não medida nesta rodada', exp: null };
  const exps = m.experimentos ?? [];
  const parcial = exps.some((e) => e.status !== 'concluido');
  return { tipo: parcial ? 'parcial' : 'medida', rotulo: parcial ? 'parcial' : 'medida', exp: exps[exps.length - 1]?.experiment_id ?? null };
}

export function filtrarConsultas(consultas, f, rodada) {
  const termo = f.busca.trim().toLowerCase();
  return consultas.filter((c) => {
    if (f.ap && c.antipadrao !== f.ap) return false;
    if (f.sel && c.seletividade !== f.sel) return false;
    if (f.papel && c.papel !== f.papel) return false;
    if (f.soMedidas && statusNaRodada(c, rodada).tipo === 'nao') return false;
    if (termo && !`${c.id} ${c.titulo} ${c.tabelas.join(' ')}`.toLowerCase().includes(termo)) return false;
    return true;
  });
}

export default function Catalogo() {
  const { catalogo, rodada } = useContexto();
  const [f, setF] = useState({ busca: '', ap: '', sel: '', papel: '', soMedidas: false });
  const dados = catalogo.dados;
  const consultas = dados?.consultas ?? [];
  const filtradas = useMemo(() => filtrarConsultas(consultas, f, rodada), [consultas, f, rodada]);
  const aps = useMemo(() => [...new Set(consultas.map((c) => c.antipadrao))].sort(), [consultas]);
  const papeis = useMemo(() => [...new Set(consultas.map((c) => c.papel))].sort(), [consultas]);
  const upd = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }));

  if (catalogo.carregando) return <div className="pagina"><Carregando /></div>;
  if (catalogo.erro) return <div className="pagina"><Aviso tipo="erro" titulo="Catálogo indisponível">{mensagemDeErro(catalogo.erro)}</Aviso></div>;

  const origemContagem = (extra = '') => `contagem das consultas de ${dados.fontes.map((x) => x.arquivo).join(' + ')}${extra}`;

  return (
    <div className="pagina">
      <header>
        <div>
          <h1>Catálogo de consultas</h1>
          <p>Cada consulta pertence a uma classe de antipadrão e tem original, reescrita e índice declarados pelo autor.</p>
        </div>
        <span className="sub">Fontes: {dados.fontes.map((x) => `${x.arquivo} (v${x.versao}, banco ${x.banco})`).join(' · ')}</span>
      </header>

      <section className="cartao" aria-labelledby="t-dist">
        <header><h2 id="t-dist">Distribuição por antipadrão e seletividade</h2></header>
        <div className="corpo sem-padding rolagem">
          <table className="tabela">
            <caption>Quantidade de consultas. {dados.legenda_seletividade ? `Seletividade: ${dados.legenda_seletividade}` : null}</caption>
            <thead>
              <tr>
                <th scope="col">Antipadrão</th>
                {SELETIVIDADES.map((s) => <th key={s} scope="col" className="n">{rotuloSeletividade(s)}</th>)}
                <th scope="col" className="n">Total</th>
              </tr>
            </thead>
            <tbody>
              {aps.map((ap) => {
                const doAp = consultas.filter((c) => c.antipadrao === ap);
                return (
                  <tr key={ap}>
                    <th scope="row">
                      <span className="mono">{ap}</span>{' '}
                      <span className="sub">{dados.descricoes_antipadrao?.[ap] ?? ''}</span>
                    </th>
                    {SELETIVIDADES.map((s) => (
                      <td key={s} className="n"><Num valor={doAp.filter((c) => c.seletividade === s).length} origem={origemContagem(` com antipadrao=${ap}, seletividade=${s}`)} rotulo={`${ap} ${s}`} /></td>
                    ))}
                    <td className="n"><Num valor={doAp.length} origem={origemContagem(` com antipadrao=${ap}`)} rotulo={`${ap} total`} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="cartao" aria-labelledby="t-lista">
        <header>
          <h2 id="t-lista">Consultas</h2>
          <span className="sub" role="status"><Num valor={filtradas.length} origem={origemContagem(' que passam pelos filtros')} rotulo="consultas exibidas" /> de <Num valor={consultas.length} origem={origemContagem()} rotulo="consultas no catálogo" /></span>
        </header>
        <div className="corpo">
          <div className="campo-linha" role="search" aria-label="Filtros do catálogo">
            <label className="campo"><span>Busca</span><input type="search" name="busca" value={f.busca} onChange={upd('busca')} placeholder="id, título ou tabela" /></label>
            <label className="campo"><span>Antipadrão</span>
              <select name="selecao" value={f.ap} onChange={upd('ap')}><option value="">todos</option>{aps.map((a) => <option key={a} value={a}>{a}</option>)}</select>
            </label>
            <label className="campo"><span>Seletividade</span>
              <select name="selecao" value={f.sel} onChange={upd('sel')}><option value="">todas</option>{SELETIVIDADES.map((s) => <option key={s} value={s}>{rotuloSeletividade(s)}</option>)}</select>
            </label>
            <label className="campo"><span>Papel</span>
              <select name="selecao" value={f.papel} onChange={upd('papel')}><option value="">todos</option>{papeis.map((p) => <option key={p} value={p}>{rotuloPapel(p)}</option>)}</select>
            </label>
            <label className="campo"><span>Rodada {rodada ?? ''}</span>
              <span><input type="checkbox" name="opcao" checked={f.soMedidas} onChange={upd('soMedidas')} disabled={!rodada} /> só as medidas</span>
            </label>
          </div>
        </div>
        {filtradas.length === 0 ? (
          <div className="corpo"><EstadoVazio titulo="Nenhuma consulta passa pelos filtros" motivo="Ajuste ou limpe os filtros acima." /></div>
        ) : (
          <div className="corpo sem-padding rolagem">
            <table className="tabela">
              <thead>
                <tr><th scope="col">Id</th><th scope="col">Antipadrão</th><th scope="col">Título</th><th scope="col">Tabelas</th><th scope="col">Seletividade</th><th scope="col">Papel</th><th scope="col">Variantes declaradas</th><th scope="col">Rodada {rodada ?? 'n/d'}</th></tr>
              </thead>
              <tbody>
                {filtradas.map((c) => {
                  const st = statusNaRodada(c, rodada);
                  const declaradas = ['original', c.tem_reescrita && 'reescrita', c.tem_indice && 'índice', c.tem_reescrita && c.tem_indice && 'reescrita+índice', c.n_extras > 0 && `+${c.n_extras} extra${c.n_extras > 1 ? 's' : ''}`].filter(Boolean);
                  return (
                    <tr key={c.id}>
                      <th scope="row"><a className="mono" href={montarHash('consulta', c.id, { rodada })}>{c.id}</a></th>
                      <td title={dados.descricoes_antipadrao?.[c.antipadrao]}><span className="mono">{c.antipadrao}</span></td>
                      <td>{c.titulo}</td>
                      <td className="mono">{c.tabelas.join(', ')}</td>
                      <td>{rotuloSeletividade(c.seletividade)}</td>
                      <td><Badge tipo={c.papel === 'principal' ? 'acento' : c.papel === 'controle' ? 'alerta' : ''}>{rotuloPapel(c.papel)}</Badge></td>
                      <td className="sub">{declaradas.join(' · ')}</td>
                      <td>
                        <Badge tipo={st.tipo === 'medida' ? 'ok' : st.tipo === 'parcial' ? 'alerta' : ''}>{st.rotulo}</Badge>
                        {st.exp != null ? <span className="origem"> exp {st.exp}</span> : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <p className="nota">Antipadrão, seletividade e papel são classificações declaradas pelo autor no catálogo; a detecção automática é a camada ① Diagnóstico.</p>
    </div>
  );
}
