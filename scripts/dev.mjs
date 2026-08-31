// Lightweight local development entry; no shell or process-manager dependency.
import { spawn, execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const root = fileURLToPath(new URL('../', import.meta.url));
const apiDir = path.join(root, 'apps/api');
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const vite = path.join(root, 'node_modules/vite/bin/vite.js');
const children = new Set();
let stopping = false;

function run(file, args, { capture = false, cwd = apiDir } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(file, args, { cwd, windowsHide: true, stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit' });
    let stdout = '';
    if (capture) {
      child.stdout.on('data', data => { stdout += data; });
      child.stderr.resume(); // Do not print configuration or connection credentials.
    }
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolve(stdout) : reject(new Error(path.basename(file) + ' exited with code ' + code)));
  });
}

function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) {
    try {
      if (process.platform === 'win32') {
        execFileSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
      } else {
        process.kill(-child.pid, 'SIGTERM');
      }
    } catch { /* The child may already have exited. */ }
  }
  console.log('[dev] Stopped owned services; reused services and PostgreSQL remain running.');
  process.exit(code);
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());

function start(file, args, cwd) {
  const child = spawn(file, args, { cwd, windowsHide: true, stdio: 'inherit', detached: process.platform !== 'win32' });
  children.add(child);
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => {
    children.delete(child);
    if (!stopping) { console.error('[dev] Service exited (' + code + ').'); stop(code || 1); }
  });
}

async function settings() {
  if (!existsSync(python)) throw new Error('Missing root .venv. Follow README: python -m venv .venv and install requirements.txt.');
  try {
    return JSON.parse(await run(python, ['-c',
      'import json; from config import settings; from sqlalchemy.engine import make_url; u=make_url(settings.database_url); print(json.dumps(dict(host=u.host, port=u.port or 5432, database=u.database, backend=u.get_backend_name(), api_port=settings.api_port, app_name=settings.app_name, app_env=settings.app_env)))',
    ], { capture: true }));
  } catch {
    throw new Error('Cannot load API settings. Install requirements.txt and configure apps/api/.env (DATABASE_URL required; environment overrides .env).');
  }
}

async function databaseReady() {
  try {
    await run(python, ['-c', 'from config import settings; from sqlalchemy import create_engine, text; engine=create_engine(settings.database_url, connect_args={"connect_timeout": 3}); c=engine.connect(); c.execute(text("select 1")); c.close()'], { capture: true });
    return true;
  } catch { return false; }
}

async function databaseUp(config) {
  if (config.backend !== 'postgresql') throw new Error('Local development requires PostgreSQL. Check DATABASE_URL.');
  if (await databaseReady()) { console.log('[db] Configured PostgreSQL is reachable.'); return; }
  if (!['localhost', '127.0.0.1', '::1'].includes(config.host) || config.port !== 5432) {
    throw new Error('Configured database is unavailable. Start that instance separately; the existing flyweave-postgres container maps local port 5432.');
  }
  try {
    await run('docker', ['start', 'flyweave-postgres'], { capture: true });
  } catch {
    throw new Error('Start Docker Desktop. If flyweave-postgres does not exist, create it using the first-install command in README, then retry npm run dev:db.');
  }
  for (let attempt = 0; attempt < 30; attempt++) {
    if (await databaseReady()) { console.log('[db] PostgreSQL is ready.'); return; }
    await delay(1000);
  }
  throw new Error('PostgreSQL connection failed. Check container logs, DATABASE_URL credentials/database, and port mapping. No data was reset.');
}

async function request(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(3000) });
  if (!response.ok) throw new Error(url + ' returned HTTP ' + response.status);
  return response;
}

async function apiReady(config) {
  try {
    const health = await (await request('http://localhost:8000/health')).json();
    const schema = await (await request('http://localhost:8000/openapi.json')).json();
    return health.status === 'healthy' && health.app_name === config.app_name && !!schema.paths?.['/approval-requests'];
  } catch { return false; }
}

async function webReady() {
  try {
    const client = await (await request('http://127.0.0.1:3000/@vite/client')).text();
    return client.toLowerCase().includes(root.replaceAll('\\', '/').replace(/\/$/, '').toLowerCase() + '/');
  } catch { return false; }
}

async function waitFor(check, name) {
  for (let attempt = 0; attempt < 30; attempt++) {
    if (await check()) return;
    await delay(1000);
  }
  throw new Error(name + ' did not become ready. See service output; ports are fixed at 8000/3000.');
}

async function check(config) {
  if (!await apiReady(config)) throw new Error('Flyweave API is not ready at localhost:8000.');
  const approvals = await (await request('http://localhost:8000/approval-requests')).json();
  if (!Array.isArray(approvals)) throw new Error('Approval API did not return an array.');
  for (const origin of ['http://localhost:3000', 'http://127.0.0.1:3000']) {
    const response = await fetch('http://localhost:8000/approval-requests', {
      method: 'OPTIONS', headers: { Origin: origin, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type' },
      signal: AbortSignal.timeout(3000),
    });
    if (!response.ok || response.headers.get('access-control-allow-origin') !== origin) throw new Error('CORS rejected ' + origin);
  }
  if (!await webReady()) throw new Error('This repository Vite server is not ready at port 3000.');
  await request('http://localhost:3000/approvals');
  console.log('[check] API healthy; approval API returned ' + approvals.length + ' persisted records; CORS passed for both local origins; frontend reachable.');
  console.log('[dev] Open http://localhost:3000/approvals (no Demo Mode badge; [] is a valid empty state).');
}

try {
  const command = process.argv[2];
  if (!['api', 'db', 'migrate', 'seed', 'check', 'all'].includes(command)) throw new Error('Use npm run dev:api / dev:db / dev:migrate / dev:seed / dev:check / dev:all.');
  const config = await settings();
  if (['api', 'all', 'check'].includes(command) && config.api_port !== 8000) throw new Error('Frontend API URLs are fixed at localhost:8000. Set API_PORT=8000 for local development.');
  if (command === 'all' && (config.app_env !== 'development' || !['localhost', '127.0.0.1', '::1'].includes(config.host))) throw new Error('dev:all only migrates a local development database. Use the individual commands for other environments.');
  if (command === 'db' || command === 'all') await databaseUp(config);
  if (command === 'migrate' || command === 'all') await run(python, ['-m', 'alembic', 'upgrade', 'head']);
  if (command === 'seed') {
    // Existing seed_data clears demo rows. Never run it automatically or over existing data.
    await run(python, ['-c',
      'from database import engine, SessionLocal, Base; import models; from sqlalchemy import select, func; from seed_data import seed_demo_data; db=SessionLocal(); occupied=[t.name for t in Base.metadata.sorted_tables if db.scalar(select(func.count()).select_from(t))]; import sys; sys.exit("Refusing seed: database is not empty (" + ", ".join(occupied) + "). Existing seed_data deletes demo rows; use a fresh development database.") if occupied else None; print(seed_demo_data(db)); db.close()',
    ]);
  }
  if (command === 'api') start(python, ['main.py'], apiDir);
  if (command === 'all') {
    if (!existsSync(vite)) throw new Error('Missing frontend dependencies. Run npm ci at the repository root.');
    if (await apiReady(config)) console.log('[api] Reusing existing Flyweave API on 8000.');
    else { start(python, ['main.py'], apiDir); await waitFor(() => apiReady(config), 'API'); }
    if (await webReady()) console.log('[web] Reusing this repository Vite server on 3000.');
    else { start(process.execPath, [vite], path.join(root, 'apps/web')); await waitFor(webReady, 'Frontend'); }
    await check(config);
    if (children.size) console.log('[dev] Ctrl+C stops only services started by this command. PostgreSQL is kept running.');
  }
  if (command === 'check') await check(config);
} catch (error) {
  console.error('[dev] ' + error.message);
  stop(1);
}
