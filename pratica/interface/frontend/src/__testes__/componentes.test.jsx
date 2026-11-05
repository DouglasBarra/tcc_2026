// Dados SINTETICOS: so existem neste arquivo de teste. Verificam a regra "sem dado -> estado vazio" e a rastreabilidade.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Num } from '../componentes/Num.jsx';
import { EstadoVazio } from '../componentes/EstadoVazio.jsx';
import TabelaComparacao from '../telas/abas/TabelaComparacao.jsx';
import RegistroColeta from '../telas/abas/RegistroColeta.jsx';
import { PainelDiagnostico, ResultadoDiagnostico } from '../telas/abas/Diagnostico.jsx';
import PlanoOperadores, { ordenar } from '../telas/abas/PlanoOperadores.jsx';
import { statusNaRodada, filtrarConsultas } from '../telas/Catalogo.jsx';
import { diferencas } from '../telas/Ambiente.jsx';
import { motivoSemNativo } from '../telas/abas/Prescricao.jsx';
import { opcoesDePlano } from '../telas/abas/Diagnostico.jsx';
import { linhasComparacao } from '../lib/validacao.js';

describe('Num: numero com origem', () => {
  it('mostra o numero formatado e a origem no tooltip e no aria-label', () => {
    render(<Num valor={800856} origem={{ rodada: 'r1', experiment_id: 3, run_ids: [4, 9], tabela: 'measurement', metrica: 'logical_reads' }} rotulo="leituras lógicas" />);
    const el = screen.getByText('800.856');
    expect(el).toHaveAttribute('data-origem', 'rodada r1 · experimento 3 · runs 4, 9 · measurement.logical_reads');
    expect(el).toHaveAccessibleName(/leituras lógicas: 800\.856\. Origem: rodada r1/);
    expect(el).toHaveAttribute('tabindex', '0'); // alcancavel pelo teclado
  });

  it.each([[null], [undefined], [NaN]])('sem dado (%s) mostra travessao, nunca 0', (v) => {
    const { container } = render(<Num valor={v} origem="x" rotulo="mediana" />);
    expect(container.textContent).toBe('n/d');
    expect(container.textContent).not.toMatch(/0/);
    expect(container.firstChild).toHaveAccessibleName('mediana: sem dado');
  });

  it('zero medido continua sendo zero', () => {
    const { container } = render(<Num valor={0} origem="x" rotulo="n" />);
    expect(container.textContent).toBe('0');
  });
});

describe('estado vazio honesto', () => {
  it('anuncia o que falta e por que', () => {
    render(<EstadoVazio titulo="Sem medições" motivo="A consulta não foi medida nesta rodada." />);
    const r = screen.getByRole('status');
    expect(r).toHaveTextContent('Sem medições');
    expect(r).toHaveTextContent('não foi medida');
  });
});

const g = (variante, regime, metrica, mediana, run_ids) => ({ variante, regime, metrica, mediana, q1: mediana, q3: mediana, n: 4, run_ids, fonte: 'tcclab.analise' });
const VAL = {
  disponivel: true,
  variantes: [{ nome: 'original', kind: 'original' }, { nome: 'prescricao', kind: 'prescricao' }],
  grupos: [g('original', 'quente', 'logical_reads', 1000, [11, 15]), g('prescricao', 'quente', 'logical_reads', 100, [14])],
  comparacoes: [{ experiment_id: 8, regime: 'quente', metrica: 'logical_reads', baseline: 'original', tratamento: 'prescricao', razao: 0.1, razao_reg: 0.1, reducao_pct: 90, ic_lo: 0.1, ic_hi: 0.1, n_antes: 8, n_depois: 4, run_ids_antes: [11, 15], run_ids_depois: [14], regra_zero: '', atipicos_antes: 0, atipicos_depois: 0 }],
  veredictos: [],
};

describe('TabelaComparacao', () => {
  it('sem linhas mostra estado vazio e nenhum numero', () => {
    const { container } = render(<TabelaComparacao linhas={[]} regimeRotulo="Frio" antesNome="original" depoisNome="prescricao" rodada="r1" experimentId={8} />);
    expect(screen.getByRole('status')).toHaveTextContent('Sem medições no regime frio');
    expect(container.querySelector('table')).toBeNull();
  });

  it('cada numero da linha tem origem com run_ids', () => {
    const linhas = linhasComparacao(VAL, 'quente', 'prescricao');
    const { container } = render(<TabelaComparacao linhas={linhas} regimeRotulo="Quente" antesNome="original" depoisNome="prescricao" rodada="r1" experimentId={8} />);
    const nums = [...container.querySelectorAll('.num:not(.vazio-num)')];
    expect(nums.length).toBeGreaterThan(5);
    for (const n of nums) expect(n.dataset.origem).toMatch(/run/);
    expect(within(container).getAllByText('1.000').length).toBeGreaterThan(0);
    expect(within(container).getByText('\u221290,0\u00a0%', { normalizer: (t) => t })).toBeInTheDocument(); // variacao = -reducao
    expect(container.textContent).not.toContain('exp 8'); // versao resumida: origem so no tooltip de cada numero
    fireEvent.click(screen.getByRole('button', { name: /origem de cada número/ }));
    expect(container.textContent).toContain('exp 8');
  });

  it('sem comparacao do modulo de analise nao inventa razao nem IC', () => {
    const linhas = linhasComparacao({ ...VAL, comparacoes: [] }, 'quente', 'prescricao');
    const { container } = render(<TabelaComparacao linhas={linhas} regimeRotulo="Quente" antesNome="original" depoisNome="prescricao" rodada="r1" experimentId={8} />);
    const vazios = container.querySelectorAll('.vazio-num');
    expect(vazios.length).toBeGreaterThanOrEqual(2); // razao e variacao
    expect(container.textContent).not.toMatch(/0,10/);
  });
});

describe('RegistroColeta', () => {
  const reg = [
    { linha: 1, tentativa: 1, experiment_id: 1, status: 'concluido', deriva_a1_a2: 0.256, descartado_por_deriva: true, equivalencia: { aplicavel: true, ok: true, linhas: 11354 }, nativo_ddl: 'CREATE INDEX x', notas: [], duracao_s: 313, erro: null },
    { linha: 2, tentativa: 2, experiment_id: 2, status: 'concluido', deriva_a1_a2: 0.05, descartado_por_deriva: false, equivalencia: { aplicavel: true, ok: true, linhas: 11354 }, nativo_ddl: null, notas: ['otimizador nao sugeriu indice'], duracao_s: 328, erro: null },
    { linha: 3, tentativa: 0, experiment_id: null, status: 'invalida_equivalencia', deriva_a1_a2: null, descartado_por_deriva: null, equivalencia: { aplicavel: true, ok: false, linhas: 3 }, nativo_ddl: null, notas: [], duracao_s: null, erro: null },
  ];
  it('mostra tentativa descartada, nativo ausente e consulta invalidada pela equivalencia', () => {
    const { container } = render(<RegistroColeta validacao={{ registro_coleta: reg }} rodada="r1" />);
    expect(screen.getByText('sim: substituída por nova tentativa')).toBeInTheDocument();
    expect(screen.getAllByText('otimizador não sugeriu índice')).toHaveLength(2);
    expect(screen.getByText('invalidada: resultado diferente')).toBeInTheDocument();
    expect(screen.getByText(/divergente: não medida/)).toBeInTheDocument();
    expect(container.textContent).toContain('25,6');
  });
  it('sem registro: estado vazio', () => {
    render(<RegistroColeta validacao={{ registro_coleta: [] }} rodada="r1" />);
    expect(screen.getByRole('status')).toHaveTextContent('Sem registro da coleta');
  });
});

describe('Diagnostico', () => {
  it('modulo indisponivel: estado "ainda nao disponivel", sem achados inventados', () => {
    render(<PainelDiagnostico modulo={{ disponivel: false, motivo: 'módulo de diagnóstico ainda não disponível' }} resultado={null} />);
    expect(screen.getByRole('status')).toHaveTextContent('Módulo de diagnóstico ainda não disponível');
    expect(screen.queryByRole('table')).toBeNull();
  });

  const R = {
    achados: [{ id: 'S1-1', regra: 'S1', antipadrao: 'AP3', gravidade: 'alta', nivel: 'indicio', fontes: ['sql'], evidencia: 'SELECT * sobre dbo.Posts', explicacao: 'colunas nao listadas', linha_sql: 1, notas: [], corrobora: [] }],
    sql_analisavel: true, motivo_sql: null, plano: 'nenhum', regras_executadas: ['S1'], regras_ignoradas: { P1: 'sem plano de execução fornecido' }, avisos: [], limiares: { p1_linhas_minimas: 100000 },
  };
  it('mostra achados, regras ignoradas e a comparacao declarado x detectado', () => {
    render(<ResultadoDiagnostico r={R} declarado="AP3" />);
    expect(screen.getByText('S1-1')).toBeInTheDocument();
    expect(screen.getByText(/Regras NÃO executadas/)).toBeInTheDocument();
    expect(screen.getByText(/sem plano de execução fornecido/)).toBeInTheDocument();
    expect(screen.getByText(/declarado AP3/)).toHaveTextContent('detectado AP3');
  });
  it('sem achados avisa que isso nao prova ausencia de problemas', () => {
    render(<ResultadoDiagnostico r={{ ...R, achados: [] }} declarado="AP3" />);
    expect(screen.getByRole('status')).toHaveTextContent('não prova ausência de problemas');
  });
  it('SQL nao analisavel nunca vira "sem problemas"', () => {
    render(<ResultadoDiagnostico r={{ ...R, achados: [], sql_analisavel: false, motivo_sql: 'ParseError' }} />);
    expect(screen.getByRole('alert')).toHaveTextContent('NÃO analisável'.replace('NÃO analisável', 'SQL não analisável'));
  });
});

describe('Plano de execucao', () => {
  const ops = [
    { node_id: 0, operador_fisico: 'Clustered Index Scan', leituras_logicas: 500, linhas_estimadas: 10 },
    { node_id: 1, operador_fisico: 'Stream Aggregate', leituras_logicas: null, linhas_estimadas: 1 },
    { node_id: 2, operador_fisico: 'Index Seek', leituras_logicas: 3, linhas_estimadas: 5 },
  ];
  it('ordenacao coloca o que nao tem dado sempre por ultimo', () => {
    expect(ordenar(ops, 'leituras_logicas', true).map((o) => o.node_id)).toEqual([0, 2, 1]);
    expect(ordenar(ops, 'leituras_logicas', false).map((o) => o.node_id)).toEqual([2, 0, 1]);
  });
  it('plano invalido vira estado vazio', () => {
    render(<PlanoOperadores plano={{ valido: false, motivo: 'XML do plano não pôde ser lido' }} origem={{}} />);
    expect(screen.getByRole('status')).toHaveTextContent('Plano ilegível');
  });
  it('operador estimado nao exibe contadores reais como zero', () => {
    const plano = { valido: true, eh_plano_real: false, query_plan_hash: '0xAB', operadores: [{ node_id: 0, operador_fisico: 'Index Scan', objeto: 'dbo.T.ix', linhas_estimadas: 100, linhas_reais: null, execucoes: null, leituras_logicas: null, custo_subarvore: 0.5, razao_estimado_real: null, predicado: null }], avisos: [], indices_ausentes: [] };
    const { container } = render(<PlanoOperadores plano={plano} origem={{ nota: 'teste' }} />);
    expect(container.querySelectorAll('table .vazio-num').length).toBe(4); // reais, execucoes, leituras, est./real
  });
});

describe('Catalogo e opcoes', () => {
  const C = [
    { id: 'F1', antipadrao: 'AP1', titulo: 'YEAR sobre CreationDate', tabelas: ['dbo.Posts'], seletividade: 'alta', papel: 'principal', medida_em: [{ rodada: 'r1', experimentos: [{ experiment_id: 1, status: 'concluido' }] }] },
    { id: 'F5', antipadrao: 'AP1', titulo: 'CONVERT', tabelas: ['dbo.Votes'], seletividade: 'alta', papel: 'controle', medida_em: [{ rodada: 'r1', experimentos: [{ experiment_id: 2, status: 'executando' }] }] },
    { id: 'L1', antipadrao: 'NENHUM', titulo: 'chave primaria', tabelas: ['dbo.Users'], seletividade: 'alta', papel: 'limpa', medida_em: [] },
  ];
  it('status na rodada: medida, parcial ou nao medida', () => {
    expect(statusNaRodada(C[0], 'r1').tipo).toBe('medida');
    expect(statusNaRodada(C[1], 'r1').tipo).toBe('parcial');
    expect(statusNaRodada(C[2], 'r1').tipo).toBe('nao');
    expect(statusNaRodada(C[0], 'outra').tipo).toBe('nao');
  });
  it('filtros por antipadrao, papel, busca e so medidas', () => {
    const f = { busca: '', ap: '', sel: '', papel: '', soMedidas: false };
    expect(filtrarConsultas(C, f, 'r1')).toHaveLength(3);
    expect(filtrarConsultas(C, { ...f, papel: 'controle' }, 'r1').map((c) => c.id)).toEqual(['F5']);
    expect(filtrarConsultas(C, { ...f, soMedidas: true }, 'r1').map((c) => c.id)).toEqual(['F1', 'F5']);
    expect(filtrarConsultas(C, { ...f, busca: 'votes' }, 'r1').map((c) => c.id)).toEqual(['F5']);
  });
  it('planos: o salvo real vem primeiro; plano de medicao entra uma vez por plan_id', () => {
    const o = opcoesDePlano(
      { planos_salvos: [{ rotulo: 'original.estimado' }, { rotulo: 'original.real' }] },
      { runs: [{ plan_id: 3, run_id: 7, variante: 'original', regime: 'quente' }, { plan_id: 3, run_id: 8, variante: 'original', regime: 'frio' }, { plan_id: null, run_id: 9 }] },
    );
    expect(o.map((x) => x.id)).toEqual(['salvo:original.real', 'salvo:original.estimado', 'plano:3']);
  });
  it('motivo da ausencia do braco nativo usa so o que a coleta gravou', () => {
    const val = { disponivel: true, experimento: { experiment_id: 2 }, registro_coleta: [{ experiment_id: 2, notas: ['otimizador nao sugeriu indice'] }] };
    expect(motivoSemNativo({ notas_variantes: [], tem_indice: true }, val)).toMatch(/não sugeriu índice/);
    expect(motivoSemNativo({ notas_variantes: [], tem_indice: true }, { disponivel: false })).toMatch(/Sem coleta/);
    expect(motivoSemNativo({ notas_variantes: [], tem_indice: false }, { ...val, registro_coleta: [] })).toMatch(/não tem índice/);
  });
  it('ambientes: so aponta campos que realmente diferem', () => {
    expect(diferencas({ maxdop: 0, compat_level: 160 }, { maxdop: 1, compat_level: 160 })).toEqual(['maxdop']);
    expect(diferencas(null, {})).toEqual([]);
  });
});
