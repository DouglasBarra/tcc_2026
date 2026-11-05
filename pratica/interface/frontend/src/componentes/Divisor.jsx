import { useRef } from 'react';

/** Divisor horizontal redimensionavel por ponteiro e por teclado (setas). Informa a nova altura em px. */
export function Divisor({ altura, onChange, min = 120, max = 700, rotulo = 'Redimensionar o editor' }) {
  const inicio = useRef(null);
  const limitar = (v) => Math.max(min, Math.min(max, v));
  return (
    <div
      className="divisor"
      role="separator"
      aria-orientation="horizontal"
      aria-label={rotulo}
      aria-valuenow={Math.round(altura)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        inicio.current = { y: e.clientY, h: altura };
      }}
      onPointerMove={(e) => {
        if (inicio.current) onChange(limitar(inicio.current.h + e.clientY - inicio.current.y));
      }}
      onPointerUp={() => { inicio.current = null; }}
      onKeyDown={(e) => {
        if (e.key === 'ArrowUp') { e.preventDefault(); onChange(limitar(altura - 24)); }
        if (e.key === 'ArrowDown') { e.preventDefault(); onChange(limitar(altura + 24)); }
      }}
    />
  );
}
