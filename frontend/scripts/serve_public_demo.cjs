const { spawn } = require('node:child_process');
const path = require('node:path');

const repositoryRoot = path.resolve(__dirname, '../..');
const launcher = path.join(repositoryRoot, 'tests/support/serve_public_demo.py');
const child = spawn(process.env.PYTHON || 'python', [launcher], {
  cwd: repositoryRoot,
  env: { ...process.env, PYTHONPATH: path.join(repositoryRoot, 'backend/src') },
  stdio: 'inherit',
  shell: false,
});

let stopping = false;
for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) {
  process.on(signal, () => {
    stopping = true;
    child.kill(signal);
  });
}
child.on('error', (error) => {
  console.error(`Could not start the public demo test server: ${error.message}`);
  process.exitCode = 1;
});
child.on('close', (code) => process.exit(stopping ? 0 : code ?? 1));
