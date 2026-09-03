// Run pytest through the project virtualenv, never the ambient system Python.
// The system interpreter may lack project dependencies such as alembic.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const python = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
if (!existsSync(python)) {
  console.error('Missing root .venv. Follow README: python -m venv .venv and install requirements.txt.');
  process.exit(1);
}
const child = spawn(python, ['-m', 'pytest', ...process.argv.slice(2)], { cwd: root, stdio: 'inherit', windowsHide: true });
child.on('error', (error) => { console.error(error.message); process.exit(1); });
child.on('exit', (code) => process.exit(code ?? 1));
