import { defineConfig } from '@playwright/test';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendRoot = fileURLToPath(new URL('.', import.meta.url));
const server = { command: 'node scripts/serve_browser.cjs', cwd: frontendRoot, timeout: 30_000 };

export default defineConfig({
  testDir: resolve(frontendRoot, 'tests/e2e'),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: { baseURL: 'http://127.0.0.1:8765', headless: true, trace: 'retain-on-failure' },
  // first-run.spec.ts runs against a second fixture server holding an empty ledger.
  projects: [
    { name: 'fixture', testIgnore: /first-run\.spec\.ts$/ },
    {
      name: 'empty-ledger',
      testMatch: /first-run\.spec\.ts$/,
      use: { baseURL: 'http://127.0.0.1:8766' },
    },
  ],
  webServer: [
    { ...server, url: 'http://127.0.0.1:8765', reuseExistingServer: false },
    {
      ...server,
      url: 'http://127.0.0.1:8766',
      reuseExistingServer: false,
      env: { AUTONOMO_FIXTURE_EMPTY: '1', AUTONOMO_FIXTURE_PORT: '8766' },
    },
  ],
});
