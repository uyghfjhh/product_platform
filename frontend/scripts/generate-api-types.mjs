import { spawnSync } from 'node:child_process';
import { resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');
const result = spawnSync(resolve(root, '.venv/bin/python'), [
  '-m', 'platform_app.export_contracts', '--output', 'frontend/src/platform/contracts.generated.ts',
  ...(process.argv.includes('--check') ? ['--check'] : []),
], { cwd: root, env: { ...process.env, PYTHONPATH: resolve(root, 'backend') }, stdio: 'inherit' });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
