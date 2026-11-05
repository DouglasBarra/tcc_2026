import { useRef } from 'react';

/** Abas acessiveis (WAI-ARIA tabs): setas, Home/End, foco visivel. O painel e renderizado por quem usa. */
export function Abas({ abas, ativa, onChange, rotulo, prefixo = 'aba' }) {
  const refs = useRef({});
  const idx = Math.max(0, abas.findIndex((a) => a.id === ativa));

  const mover = (i) => {
    const alvo = abas[(i + abas.length) % abas.length];
    onChange(alvo.id);
    refs.current[alvo.id]?.focus();
  };
  const tecla = (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); mover(idx + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); mover(idx - 1); }
    else if (e.key === 'Home') { e.preventDefault(); mover(0); }
    else if (e.key === 'End') { e.preventDefault(); mover(abas.length - 1); }
  };

  return (
    <div className="abas" role="tablist" aria-label={rotulo} onKeyDown={tecla}>
      {abas.map((a, i) => (
        <button
          key={a.id}
          ref={(el) => { refs.current[a.id] = el; }}
          id={`${prefixo}-${a.id}`}
          type="button"
          role="tab"
          className="aba"
          aria-selected={a.id === ativa}
          aria-controls={`${prefixo}-painel-${a.id}`}
          tabIndex={a.id === ativa ? 0 : -1}
          onClick={() => onChange(a.id)}
        >
          {a.circulo ? <span className="circulo" aria-hidden="true">{a.circulo}</span> : null}
          <span>{a.rotulo}</span>
          {a.contador != null ? <span className="contador" aria-label={`${a.contador} itens`}>{a.contador}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function PainelAba({ id, prefixo = 'aba', children }) {
  return (
    <div role="tabpanel" id={`${prefixo}-painel-${id}`} aria-labelledby={`${prefixo}-${id}`} tabIndex={0} className="painel-corpo">
      {children}
    </div>
  );
}
