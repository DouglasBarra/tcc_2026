// Formatadores pt-BR. Regra de ouro: sem dado => travessao (nunca "0"); numero com origem fica em <Num>.
export const VAZIO = 'n/d'; // valor ausente (sem travessão, pedido do autor)
const NBSP = ' ';
const MENOS = '−'; // −

const cacheFmt = new Map();
function nf(opcoes) {
  const chave = JSON.stringify(opcoes);
  if (!cacheFmt.has(chave)) cacheFmt.set(chave, new Intl.NumberFormat('pt-BR', opcoes));
  return cacheFmt.get(chave);
}

export function ehNumero(x) {
  return typeof x === 'number' && Number.isFinite(x);
}

function arredondaZero(x, casas) {
  // evita "-0" e "-0,0" depois do arredondamento
  return Number(x.toFixed(casas)) === 0 ? 0 : x;
}

/** Numero com `casas` decimais fixas e separador de milhar: 1234.5 -> "1.234,5". */
export function fmtNum(x, casas = 0) {
  if (!ehNumero(x)) return VAZIO;
  return nf({ minimumFractionDigits: casas, maximumFractionDigits: casas }).format(arredondaZero(x, casas));
}

/** Inteiro. Valores nao inteiros sao mostrados com ate 2 decimais para nao esconder informacao. */
export function fmtInt(x) {
  if (!ehNumero(x)) return VAZIO;
  return Number.isInteger(x) ? fmtNum(x, 0) : fmtNum(x, 2);
}

/** Algarismos significativos (padrao 3): 0.05 -> "0,050"; 1234.5 -> "1.235"; 0.01234 -> "0,0123". */
export function fmtSig(x, sig = 3) {
  if (!ehNumero(x)) return VAZIO;
  if (x === 0) return '0';
  if (Math.abs(x) >= 10 ** sig) return fmtNum(x, 0);
  return nf({ minimumSignificantDigits: Math.min(2, sig), maximumSignificantDigits: sig }).format(x);
}

/** Razao depois/antes. */
export function fmtRazao(x) {
  return fmtSig(x, 3);
}

/**
 * Percentual ja em pontos percentuais: -94.7 -> "−94,7 %". `sinal` forca "+" nos positivos.
 * Nunca esconde informacao pelo arredondamento: 99,995 nao vira "100,0 %" e 0,003 nao vira "0,0 %" (usa 3 casas).
 */
export function fmtPct(x, casas = 1, sinal = false) {
  if (!ehNumero(x)) return VAZIO;
  let c = casas;
  const arred = Number(x.toFixed(c));
  if ((Math.abs(arred) === 100 && Math.abs(x) !== 100) || (arred === 0 && x !== 0)) c = Math.max(casas, 3);
  const r = arredondaZero(x, c);
  const corpo = nf({ minimumFractionDigits: c, maximumFractionDigits: c }).format(Math.abs(r));
  const s = r < 0 ? MENOS : sinal && r > 0 ? '+' : '';
  return `${s}${corpo}${NBSP}%`;
}

/** Intervalo: [lo; hi]. Se algum extremo faltar, nao inventa: devolve travessao. */
export function fmtIC(lo, hi, f = fmtRazao) {
  if (!ehNumero(lo) || !ehNumero(hi)) return VAZIO;
  return `[${f(lo)}; ${f(hi)}]`;
}

export function fmtMs(x, casas = 0) {
  if (!ehNumero(x)) return VAZIO;
  return `${fmtNum(x, casas)}${NBSP}ms`;
}

export function fmtUs(x) {
  if (!ehNumero(x)) return VAZIO;
  return `${fmtNum(x, 0)}${NBSP}µs`;
}

export function fmtBytes(x) {
  if (!ehNumero(x)) return VAZIO;
  const un = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let v = Math.abs(x);
  let i = 0;
  while (v >= 1024 && i < un.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${fmtNum(x < 0 ? -v : v, i === 0 ? 0 : 1)}${NBSP}${un[i]}`;
}

/** Valor "p": < 0,001 vira "< 0,001". */
export function fmtP(p) {
  if (!ehNumero(p)) return VAZIO;
  if (p < 0.001) return `<${NBSP}0,001`;
  return fmtNum(p, 3);
}

const RE_DATA = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/;

/** "2026-10-25 17:52:11.123" (UTC, como gravado) -> "25/10/2026 17:52:11 UTC". */
export function fmtData(s) {
  if (typeof s !== 'string') return VAZIO;
  const m = RE_DATA.exec(s);
  if (!m) return VAZIO;
  return `${m[3]}/${m[2]}/${m[1]} ${m[4]}:${m[5]}:${m[6]} UTC`;
}

/** Segundos -> "há 12 s", "há 3 min", "há 2 h", "há 4 d". */
export function fmtHa(segundos) {
  if (!ehNumero(segundos) || segundos < 0) return VAZIO;
  if (segundos < 90) return `há ${Math.round(segundos)} s`;
  if (segundos < 5400) return `há ${Math.round(segundos / 60)} min`;
  if (segundos < 172800) return `há ${Math.round(segundos / 3600)} h`;
  return `há ${Math.round(segundos / 86400)} d`;
}

export function fmtLista(ids) {
  if (!Array.isArray(ids) || ids.length === 0) return VAZIO;
  return ids.map((i) => fmtNum(i, 0).replace(/\./g, '')).join(', '); // run_id nao leva separador de milhar
}

export function fmtHash(h, n = 10) {
  if (typeof h !== 'string' || !h) return VAZIO;
  return h.length > n + 2 ? `${h.slice(0, n)}…` : h;
}

const METRICAS = {
  logical_reads: ['Leituras lógicas', 'leituras'],
  physical_reads: ['Leituras físicas', 'leituras'],
  read_ahead_reads: ['Read-ahead', 'leituras'],
  cpu_ms: ['CPU (ms)', 'ms'],
  elapsed_ms: ['Tempo de execução (ms)', 'ms'],
  cpu_us: ['CPU (µs)', 'µs'],
  elapsed_us: ['Tempo de execução (µs)', 'µs'],
  log_bytes: ['Log da transação (bytes)', 'bytes'],
};

export function rotuloMetrica(m) {
  return METRICAS[m]?.[0] ?? m;
}

export function unidadeMetrica(m) {
  return METRICAS[m]?.[1] ?? '';
}

export function rotuloRegime(r) {
  return { quente: 'Quente', frio: 'Frio' }[r] ?? r ?? VAZIO;
}

export function rotuloEstadoRodada(e) {
  return (
    {
      em_andamento: 'em andamento',
      interrompida: 'interrompida',
      concluida: 'concluída',
      com_erro: 'com erro',
      vazia: 'vazia',
    }[e] ?? e ?? VAZIO
  );
}

export function rotuloTipoRodada(t) {
  return { coleta: 'coleta', piloto: 'piloto', ao_vivo: 'ao vivo', abortada: 'abortada' }[t] ?? t ?? VAZIO;
}

export function rotuloSeletividade(s) {
  return { alta: 'alta', media: 'média', baixa: 'baixa' }[s] ?? s ?? VAZIO;
}

export function rotuloPapel(p) {
  return { principal: 'principal', controle: 'controle', limpa: 'limpa (sem antipadrão)' }[p] ?? p ?? VAZIO;
}

export function rotuloOperacao(o) {
  return { insert: 'INSERT', update: 'UPDATE', delete: 'DELETE' }[o] ?? o ?? VAZIO;
}
