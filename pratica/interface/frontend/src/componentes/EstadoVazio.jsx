/** Estado vazio honesto: diz o que falta e por que, sem preencher com dado inventado. */
export function EstadoVazio({ titulo, motivo, children }) {
  return (
    <div className="vazio" role="status">
      <strong>{titulo}</strong>
      {motivo ? <span>{motivo}</span> : null}
      {children}
    </div>
  );
}

export function Aviso({ tipo = 'info', titulo, children }) {
  return (
    <div className={`aviso ${tipo}`} role={tipo === 'erro' ? 'alert' : 'note'}>
      {titulo ? <strong>{titulo}</strong> : null}
      {children}
    </div>
  );
}

export function Carregando({ texto = 'Carregando…' }) {
  return (
    <p className="carregando" role="status" aria-live="polite">
      {texto}
    </p>
  );
}

export function Badge({ tipo = '', children, titulo }) {
  return (
    <span className={`badge ${tipo}`} title={titulo}>
      {children}
    </span>
  );
}
