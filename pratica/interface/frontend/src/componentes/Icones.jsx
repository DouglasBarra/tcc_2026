// Icones SVG minimos (sem dependencia). Decorativos: aria-hidden; o botao que os usa tem rotulo textual.
const base = { width: 16, height: 16, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true, focusable: false };

export const IconeSol = () => (
  <svg {...base}><circle cx="8" cy="8" r="3" /><path d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M12.6 3.4l-1.3 1.3M4.7 11.3l-1.3 1.3" /></svg>
);
export const IconeLua = () => (
  <svg {...base}><path d="M13.5 9.6A5.8 5.8 0 0 1 6.4 2.5a5.8 5.8 0 1 0 7.1 7.1Z" /></svg>
);
export const IconeAuto = () => (
  <svg {...base}><circle cx="8" cy="8" r="6" /><path d="M8 2v12" /></svg>
);
export const IconeMenu = () => (
  <svg {...base}><path d="M2.5 4h11M2.5 8h11M2.5 12h11" /></svg>
);
export const IconeRaio = () => (
  <svg {...base}><path d="M9 1.5 3.5 9H8l-1 5.5L12.5 7H8Z" /></svg>
);
export const IconeSeta = ({ cima = false }) => (
  <svg {...base} width={12} height={12}><path d={cima ? 'M3 10l5-5 5 5' : 'M3 6l5 5 5-5'} /></svg>
);
export const IconeReset = () => (
  <svg {...base}><path d="M3 8a5 5 0 1 0 1.6-3.7M3 2.5v3h3" /></svg>
);

// Icones da navegacao lateral (20 px)
const nav = { ...base, width: 20, height: 20, viewBox: '0 0 20 20', strokeWidth: 1.7 };
export const IconePainel = () => (
  <svg {...nav}><rect x="2.5" y="2.5" width="6.5" height="8" rx="1.5" /><rect x="11" y="2.5" width="6.5" height="5" rx="1.5" /><rect x="11" y="9.5" width="6.5" height="8" rx="1.5" /><rect x="2.5" y="12.5" width="6.5" height="5" rx="1.5" /></svg>
);
export const IconeCatalogo = () => (
  <svg {...nav}><ellipse cx="10" cy="4.5" rx="6.5" ry="2.5" /><path d="M3.5 4.5v11c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5v-11M3.5 10c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5" /></svg>
);
export const IconeConsulta = () => (
  <svg {...nav}><rect x="2.5" y="3" width="15" height="14" rx="2" /><path d="M6 8l2.5 2L6 12M10.5 12.5H14" /></svg>
);
export const IconeHistorico = () => (
  <svg {...nav}><path d="M3 16.5h14M5 13V9M9 13V5M13 13v-3M17 13V7" /></svg>
);
export const IconeAmbiente = () => (
  <svg {...nav}><rect x="3" y="3" width="14" height="5.5" rx="1.5" /><rect x="3" y="11.5" width="14" height="5.5" rx="1.5" /><path d="M6 5.75h.01M6 14.25h.01" /></svg>
);
