// Cliente HTTP minimo. Erros do backend vem como { codigo, detalhe }.
export class ApiError extends Error {
  constructor(status, codigo, detalhe, extra = {}) {
    super(detalhe || `HTTP ${status}`);
    this.status = status;
    this.codigo = codigo;
    this.detalhe = detalhe;
    this.extra = extra;
  }
}

async function tratar(resp) {
  let corpo = null;
  const texto = await resp.text();
  if (texto) {
    try {
      corpo = JSON.parse(texto);
    } catch {
      corpo = { detalhe: texto.slice(0, 300) };
    }
  }
  if (!resp.ok) {
    const { codigo, detalhe, ...extra } = corpo ?? {};
    throw new ApiError(resp.status, codigo ?? 'erro_http', detalhe ?? corpo?.detail ?? resp.statusText, extra);
  }
  return corpo;
}

export async function obter(url, { signal } = {}) {
  return tratar(await fetch(url, { signal, headers: { Accept: 'application/json' } }));
}

export async function enviar(url, corpo, { signal } = {}) {
  return tratar(
    await fetch(url, {
      method: 'POST',
      signal,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(corpo),
    }),
  );
}

export function montarUrl(base, params) {
  const q = Object.entries(params ?? {})
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');
  return q ? `${base}?${q}` : base;
}

export function mensagemDeErro(e) {
  if (e instanceof ApiError) return e.detalhe || `Erro ${e.status}`;
  if (e?.name === 'AbortError') return '';
  return 'Não foi possível falar com a API (o backend está rodando em 127.0.0.1:8000?).';
}
