import { useEffect, useMemo, useState } from 'react';
import { useContexto } from '../contexto.jsx';
import { useApi } from '../hooks/useApi.js';
import { montarUrl, mensagemDeErro } from '../lib/api.js';
import { montarHash, analisarHash } from '../lib/roteador.js';
import { guardar, recuperar } from '../lib/tema.js';
import { Abas, PainelAba } from '../componentes/Abas.jsx';
import { Divisor } from '../componentes/Divisor.jsx';
import { EditorSql } from '../componentes/EditorSql.jsx';
import { Aviso, Badge, Carregando, EstadoVazio } from '../componentes/EstadoVazio.jsx';
import { IconeMenu, IconeReset } from '../componentes/Icones.jsx';
import Diagnostico from './abas/Diagnostico.jsx';
import Analise from './abas/Analise.jsx';
import Prescricao from './abas/Prescricao.jsx';
import Validacao from './abas/Validacao.jsx';
import { statusNaRodada } from './Catalogo.jsx';

const ABAS = [
  { id: 'diagnostico', rotulo: 'Diagnóstico', circulo: '1' },
  { id: 'analise', rotulo: 'Análise', circulo: '2' },
  { id: 'prescricao', rotulo: 'Prescrição', circulo: '3' },
  { id: 'validacao', rotulo: 'Validação', circulo: '4' },
];

function Lateral({ catalogo, rodada, atual, aberta }) {
  const [busca, setBusca] = useState('');
  const consultas = catalogo.dados?.consultas ?? [];
  const grupos = useMemo(() => {
    const t = busca.trim().toLowerCase();
    const mapa = new Map();
    for (const c of consultas) {
      if (t && !`${c.id} ${c.titulo}`.toLowerCase().includes(t)) continue;
      if (!mapa.has(c.antipadrao)) mapa.set(c.antipadrao, []);
      mapa.get(c.antipadrao).push(c);
    }
    return [...mapa.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [consultas, busca]);
  return (
    <aside className={`lateral ${aberta ? 'aberta' : ''}`} aria-label="Consultas do catálogo">
      <div className="busca"><input type="search" name="busca" value={busca} onChange={(e) => setBusca(e.target.value)} placeholder="Buscar consulta" aria-label="Buscar consulta" style={{ width: '100%' }} /></div>
      {catalogo.carregando ? <Carregando /> : null}
      {grupos.map(([ap, lista]) => (
        <details className="grupo-ap" key={ap} open>
          <summary title={catalogo.dados?.descricoes_antipadrao?.[ap]}>{ap} <span style={{ fontWeight: 400, textTransform: 'none' }}>({lista.length})</span></summary>
          {lista.map((c) => {
            const st = statusNaRodada(c, rodada);
            return (
              <a key={c.id} className="item-consulta" href={montarHash('consulta', c.id, { rodada })} aria-current={c.id === atual ? 'true' : undefined}>
                <span className={`ponto ${st.tipo === 'medida' ? 'medida' : st.tipo === 'parcial' ? 'parcial' : ''}`} role="img" aria-label={st.rotulo} />
                <span className="id">{c.id}</span>
                <span className="tit" title={c.titulo}>{c.titulo}</span>
              </a>
            );
          })}
        </details>
      ))}
      {!catalogo.carregando && !grupos.length ? <div style={{ padding: 12 }}><EstadoVazio titulo="Nenhuma consulta" motivo="Catálogo vazio ou filtro sem resultado." /></div> : null}
    </aside>
  );
}

export default function Consulta({ rota }) {
  const ctx = useContexto();
  const { catalogo, rodada, estado, setRodada } = ctx;
  const consultas = catalogo.dados?.consultas ?? [];
  const id = rota.id ?? consultas.find((c) => c.medida_em?.some((m) => m.rodada === rodada))?.id ?? consultas[0]?.id ?? null;
  const aba = ABAS.some((a) => a.id === rota.params.aba) ? rota.params.aba : 'diagnostico';
  const exp = rota.params.exp ? Number(rota.params.exp) : undefined;

  const detalhe = useApi(id ? montarUrl(`/api/consultas/${id}`, { rodada }) : null);
  const validacao = useApi(id && rodada ? montarUrl(`/api/rodadas/${rodada}/consultas/${id}/validacao`, { experimento: exp }) : null);
  const escrita = useApi(id && rodada ? `/api/rodadas/${rodada}/consultas/${id}/escrita` : null);

  const [variante, setVariante] = useState('original');
  const [edicoes, setEdicoes] = useState({});
  const [planoSel, setPlanoSel] = useState(null);
  const [analisado, setAnalisado] = useState(null); // { texto, achados } do ultimo diagnostico: base das marcas no editor
  const [altura, setAltura] = useState(() => recuperar('altura-editor', 200));
  const [lateralAberta, setLateralAberta] = useState(false);
  useEffect(() => { setVariante('original'); setEdicoes({}); setPlanoSel(null); setAnalisado(null); }, [id]);
  useEffect(() => { guardar('altura-editor', altura); }, [altura]);

  const d = detalhe.dados;
  const planej = d?.variantes_planejadas ?? [];
  const nomes = planej.length ? planej : d ? [{ nome: 'original', sql: d.sql_original, kind: 'original' }] : [];
  const atual = nomes.find((v) => v.nome === variante) ?? nomes[0] ?? null;
  const original = nomes.find((v) => v.kind === 'original');
  const textoCatalogo = atual?.sql ?? '';
  const texto = edicoes[atual?.nome] ?? textoCatalogo;
  const modificado = atual && edicoes[atual.nome] !== undefined && edicoes[atual.nome] !== textoCatalogo;

  // marca no editor so as linhas do texto que foi de fato analisado (se o texto mudou, as marcas somem)
  const marcas = useMemo(
    () => (analisado && analisado.texto === texto
      ? (analisado.achados ?? []).filter((a) => a.linha_sql).map((a) => ({ linha: a.linha_sql, titulo: `${a.id} ${a.regra}: ${a.explicacao ?? ''}` }))
      : []),
    [analisado, texto],
  );

  const setParams = (novos) => {
    const { tela, id: i, params } = analisarHash(window.location.hash);
    window.location.hash = montarHash(tela, i ?? id, { ...params, rodada, ...novos });
  };
  const setAba = (a) => setParams({ aba: a });

  const painel = () => {
    if (!d) return null;
    const comum = { id, rodada, detalhe: d, validacao, escrita, estado, catalogo, planoSel, setPlanoSel };
    if (aba === 'diagnostico') return <Diagnostico {...comum} textoEditor={texto} textoCatalogo={original?.sql ?? ''} onAnalisado={setAnalisado} />;
    if (aba === 'analise') return <Analise {...comum} />;
    if (aba === 'prescricao') return <Prescricao {...comum} />;
    return (
      <Validacao
        {...comum} textoOriginal={edicoes.original ?? original?.sql ?? ''} textoDepois={texto} ddlDepois={atual?.ddl_aplicar ?? null}
        onRodada={(r) => { setRodada(r); }} params={rota.params} setParams={setParams}
      />
    );
  };

  return (
    <div className="consulta">
      <Lateral catalogo={catalogo} rodada={rodada} atual={id} aberta={lateralAberta} />
      <section className="area" style={{ '--h-editor': `${Math.min(altura, Math.max(120, texto.split('\n').length * 25 + 66 + (atual?.ddl_aplicar ? 34 : 0)))}px` }} aria-label={id ? `Consulta ${id}` : 'Consulta'}>
        {!id ? (
          <div className="pagina"><EstadoVazio titulo="Nenhuma consulta selecionada" motivo="O catálogo está vazio ou indisponível." /></div>
        ) : (
          <>
            <div className="editor-bloco">
              <div className="editor-barra">
                <button type="button" className="btn discreto btn-lateral" onClick={() => setLateralAberta(!lateralAberta)} aria-expanded={lateralAberta}><IconeMenu /> Consultas</button>
                <span className="titulo">{id}</span>
                {d ? <Badge tipo="acento" titulo={d.descricao_antipadrao}>{d.antipadrao}</Badge> : null}
                {d ? <span className="sub" style={{ flex: '1 1 12em', minWidth: 0 }}>{d.titulo}</span> : null}
                <div className="chips" role="group" aria-label="Variante exibida no editor">
                  {nomes.map((v) => (
                    <button key={v.nome} type="button" className="btn" aria-pressed={atual?.nome === v.nome} onClick={() => setVariante(v.nome)}>{v.nome}</button>
                  ))}
                </div>
                {modificado ? (
                  <>
                    <Badge tipo="alerta">texto modificado (só vale para o modo ao vivo)</Badge>
                    <button type="button" className="btn discreto" onClick={() => setEdicoes((e) => { const { [atual.nome]: _, ...resto } = e; return resto; })}><IconeReset /> restaurar</button>
                  </>
                ) : null}
              </div>
              {detalhe.carregando && !d ? <Carregando /> : detalhe.erro ? <Aviso tipo="erro" titulo="Consulta indisponível">{mensagemDeErro(detalhe.erro)}</Aviso> : (
                <>
                  <EditorSql valor={texto} onChange={(t) => atual && setEdicoes((e) => ({ ...e, [atual.nome]: t }))} rotulo={`Editor T-SQL da variante ${atual?.nome ?? ''} de ${id}`} marcas={marcas} />
                  {atual?.ddl_aplicar ? <div className="editor-barra" style={{ borderTop: '1px solid var(--borda)', borderBottom: 0 }}><span className="sub">DDL aplicado nesta variante:</span> <code>{atual.ddl_aplicar}</code></div> : null}
                </>
              )}
            </div>
            <Divisor altura={altura} onChange={setAltura} />
            <div className="painel-abas">
              <Abas abas={ABAS} ativa={aba} onChange={setAba} rotulo="Camadas do framework" prefixo="camada" />
              <PainelAba id={aba} prefixo="camada">
                {d ? painel() : detalhe.carregando ? <Carregando /> : <EstadoVazio titulo="Consulta não encontrada" motivo="O id não está no catálogo (catalogo.toml ou controles_limpos.toml)." />}
              </PainelAba>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
