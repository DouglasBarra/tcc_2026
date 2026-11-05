// Mede leituras logicas, CPU e elapsed de uma consulta via STATISTICS IO/TIME,
// capturando as mensagens informativas da conexao (evento 'infoMessage' do tedious).
// Uso (dentro do container node, ver scripts/measure.sh):
//   node measure.cjs --db tcc_lab --sql "SELECT ..." [--runs 5] [--warmup 1] [--cold] [--setup "CREATE INDEX ..."]
const { Connection, Request } = require('tedious');
const a = process.argv.slice(2);
const opt = (k, d) => { const i = a.indexOf('--' + k); return i < 0 ? d : (a[i + 1] && !a[i + 1].startsWith('--') ? a[i + 1] : true); };
const db = opt('db', 'tcc_lab'), sql = opt('sql'), runs = +opt('runs', 5), warmup = +opt('warmup', 1), cold = !!opt('cold', false);
if (!sql) { console.error('faltou --sql'); process.exit(2); }

const conn = new Connection({
  server: process.env.MSSQL_HOST || 'tcc-sqlserver',
  authentication: { type: 'default', options: { userName: 'sa', password: process.env.MSSQL_SA_PASSWORD } },
  options: { database: db, encrypt: true, trustServerCertificate: true, rowCollectionOnRequestCompletion: false, requestTimeout: 0 },
});
let msgs = [];
conn.on('infoMessage', (m) => msgs.push({ number: m.number, text: m.message }));

const exec = (q) => new Promise((res, rej) => {
  const r = new Request(q, (e) => (e ? rej(e) : res()));
  r.on('row', () => {});            // descarta linhas (nao interfere nas metricas do servidor)
  conn.execSqlBatch(r);   // batch puro: o SET STATISTICS persiste na sessao (execSql usa sp_executesql e perderia o SET)
});

function parse(ms) {
  const o = { logical: 0, lob: 0, physical: 0, readAhead: 0, cpuMs: 0, elapsedMs: 0, compileCpuMs: 0, compileElapsedMs: 0 };
  let mode = null;
  for (const { text } of ms) {
    if (/^\s*Table '/.test(text)) {
      const g = (re) => (text.match(re) || [0, 0])[1] | 0;
      o.logical += g(/ logical reads (\d+)/); o.lob += g(/lob logical reads (\d+)/);
      o.physical += g(/ physical reads (\d+)/); o.readAhead += g(/ read-ahead reads (\d+)/);
    } else if (/parse and compile time/i.test(text)) mode = 'compile';
    else if (/Execution Times/i.test(text)) mode = 'exec';
    const t = text.match(/CPU time = (\d+) ms,\s+elapsed time = (\d+) ms/);
    if (t) {
      if (mode === 'compile') { o.compileCpuMs += +t[1]; o.compileElapsedMs += +t[2]; }
      else if (mode === 'exec') { o.cpuMs += +t[1]; o.elapsedMs += +t[2]; }
    }
  }
  return o;
}
const median = (v) => { const s = [...v].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };

conn.connect(async (err) => {
  if (err) { console.error('conexao:', err.message); process.exit(1); }
  try {
    const setup = opt('setup', null); if (setup && setup !== true) await exec(setup);
    await exec('SET STATISTICS IO ON; SET STATISTICS TIME ON;');
    const out = [];
    for (let i = 0; i < warmup + runs; i++) {
      if (cold) await exec('DBCC DROPCLEANBUFFERS;');
      msgs = [];
      await exec(sql);
      if (i >= warmup) out.push(parse(msgs));
    }
    const summary = {};
    for (const k of Object.keys(out[0])) summary[k] = { median: median(out.map((r) => r[k])), min: Math.min(...out.map((r) => r[k])), max: Math.max(...out.map((r) => r[k])) };
    console.log(JSON.stringify({ db, sql, cold, warmup, runs, summary, raw: out }, null, 1));
    if (process.env.DEBUG_MSGS) console.error(JSON.stringify(msgs, null, 1));
    conn.close();
  } catch (e) { console.error(e.message); process.exit(1); }
});
