import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  testDir: './tests/demo',
  workers: 1,
  retries: 0,
  use: { baseURL: 'http://127.0.0.1:8766', headless: true, trace: 'retain-on-failure' },
  webServer: {
    command: 'node scripts/serve_public_demo.cjs',
    cwd: fileURLToPath(new URL('.', import.meta.url)),
    url: 'http://127.0.0.1:8766/demo/',
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
