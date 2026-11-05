import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { Abas, PainelAba } from '../componentes/Abas.jsx';
import { Divisor } from '../componentes/Divisor.jsx';

const ABAS = [{ id: 'a', rotulo: 'Diagnóstico', circulo: '1' }, { id: 'b', rotulo: 'Análise', circulo: '2' }, { id: 'c', rotulo: 'Validação', circulo: '4' }];

function Exemplo() {
  const [ativa, setAtiva] = useState('a');
  return (
    <>
      <Abas abas={ABAS} ativa={ativa} onChange={setAtiva} rotulo="Camadas" prefixo="t" />
      <PainelAba id={ativa} prefixo="t">conteúdo {ativa}</PainelAba>
    </>
  );
}

describe('abas acessiveis', () => {
  it('expoe roles, aria-selected e liga a aba ao painel', () => {
    render(<Exemplo />);
    expect(screen.getByRole('tablist', { name: 'Camadas' })).toBeInTheDocument();
    const a = screen.getByRole('tab', { name: /Diagnóstico/ });
    expect(a).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', a.id);
  });

  it('setas, Home e End movem a selecao e o foco; so a aba ativa entra na ordem de tabulacao', () => {
    render(<Exemplo />);
    const lista = screen.getByRole('tablist');
    fireEvent.keyDown(lista, { key: 'ArrowRight' });
    expect(screen.getByRole('tab', { name: /Análise/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: /Análise/ })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('tab', { name: /Diagnóstico/ })).toHaveAttribute('tabindex', '-1');
    fireEvent.keyDown(lista, { key: 'End' });
    expect(screen.getByRole('tab', { name: /Validação/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(lista, { key: 'ArrowRight' }); // da ultima volta para a primeira
    expect(screen.getByRole('tab', { name: /Diagnóstico/ })).toHaveAttribute('aria-selected', 'true');
    fireEvent.keyDown(lista, { key: 'ArrowLeft' });
    expect(screen.getByRole('tab', { name: /Validação/ })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('divisor redimensionavel', () => {
  it('e um separador com valor e responde as setas dentro dos limites', () => {
    const mudancas = [];
    render(<Divisor altura={200} onChange={(v) => mudancas.push(v)} min={120} max={260} />);
    const d = screen.getByRole('separator');
    expect(d).toHaveAttribute('aria-valuenow', '200');
    fireEvent.keyDown(d, { key: 'ArrowDown' });
    fireEvent.keyDown(d, { key: 'ArrowUp' });
    expect(mudancas).toEqual([224, 176]);
    fireEvent.keyDown(d, { key: 'ArrowUp' });
    expect(mudancas.at(-1)).toBe(176);
  });
});
