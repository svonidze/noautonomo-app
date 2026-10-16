import { afterEach, expect, test, vi } from 'vitest';
import { createMemoryHistory } from 'vue-router';
import { createAppRouter } from '../../src/shell/create-router.ts';
import { createProductionRuntime } from '../../src/shell/production-runtime.ts';
import {
  createPublicRuntime,
  publicApiUrl,
  publicHref,
  readRuntimeConfig,
  type PublicRuntimeConfig,
} from '../../src/shell/public-runtime.ts';
import {
  localeRegistry,
  localeStorageKey,
  setLocale,
  type LocaleSpec,
} from '../../src/core/i18n.ts';

const config: PublicRuntimeConfig = {
  mode: 'public_demo',
  basePath: '/demo',
  readOnly: true,
  locales: ['en', 'es', 'ru'],
  defaultLocale: 'en',
};
const cleanups: (() => void)[] = [];
afterEach(() => {
  cleanups.splice(0).forEach((fn) => fn());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
  window.history.replaceState({}, '', '/');
  setLocale('ru');
});
function runtime() {
  const value = createPublicRuntime(config);
  cleanups.push(() => value.router.options.history.destroy());
  return value;
}
function boot(value: unknown) {
  const script = document.createElement('script');
  script.id = 'autonomo-runtime';
  script.type = 'application/json';
  script.textContent = JSON.stringify(value);
  document.body.append(script);
  return script;
}

test('the shared index runtime defaults to private behavior and validates public metadata', () => {
  expect(readRuntimeConfig(null)).toBeNull();
  expect(readRuntimeConfig(boot({}))).toBeNull();
  expect(readRuntimeConfig(boot(config))).toEqual(config);
  for (const bad of [
    { ...config, readOnly: false },
    { ...config, basePath: 'https://elsewhere.invalid' },
    { ...config, locales: ['ru'] },
    { ...config, locales: ['en,es', 'ru'] },
    { mode: 'public_demo' },
    null,
    [],
  ]) {
    expect(() => readRuntimeConfig(boot(bad))).toThrow();
  }
});

test('public transport uses the real JSON HTTP client and never translates stored records', async () => {
  window.history.replaceState({}, '', '/demo/?lang=es#/dashboard');
  const fetch = vi.fn(
    async () =>
      new Response(
        JSON.stringify({
          profile_name: 'Stored English Name',
          description: 'Stored English description',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
  );
  vi.stubGlobal('fetch', fetch);
  const app = runtime();
  const value = await app.request('/api/bootstrap');
  expect(fetch).toHaveBeenCalledWith('/demo/api/bootstrap', {});
  expect(app.initialLocale()).toBe('es');
  setLocale('ru');
  app.saveLocale('ru');
  expect(value).toEqual({
    profile_name: 'Stored English Name',
    description: 'Stored English description',
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(window.location.search).toBe('?lang=ru');
});

test('public runtime blocks writes locally and does not access browser storage', async () => {
  const spies = ['getItem', 'setItem', 'removeItem'].map((method) =>
    vi.spyOn(Storage.prototype, method as 'getItem'),
  );
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  const app = runtime();
  expect(app.initialLocale()).toBe('en');
  app.saveLocale('es');
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
    await expect(
      app.request('/api/settings/profile', { method, body: '{}' }),
    ).rejects.toMatchObject({ status: 405, code: 'public_demo_read_only' });
  }
  await expect(app.refreshCalculation('2026-Q3')).rejects.toMatchObject({ status: 405 });
  expect(fetch).not.toHaveBeenCalled();
  spies.forEach((spy) => expect(spy).not.toHaveBeenCalled());
  expect(app.intakeComponent).toBeUndefined();
});

test('prefixes raw API and AEAT filing URLs once while preserving their actual IDs', () => {
  for (const route of [
    '/api/document/real-id/content',
    '/api/document/real-id/preview',
    '/api/aeat-filings/filing-id/content',
  ]) {
    expect(publicApiUrl(route)).toBe('/demo' + route);
    expect(publicHref(route, 'es')).toBe('/demo' + route);
    expect(publicApiUrl('/demo' + route)).toBe('/demo' + route);
  }
  expect(publicApiUrl('/api/expenses?period=2026-Q3&q=software')).toBe(
    '/demo/api/expenses?period=2026-Q3&q=software',
  );
  expect(publicHref('/demo/?lang=en#/review?id=1', 'ru')).toBe('/demo/?lang=ru#/review?id=1');
  expect(() => publicApiUrl('https://elsewhere.invalid/api/bootstrap')).toThrow();
  expect(() => publicApiUrl('/settings')).toThrow();
  const app = runtime();
  expect(app.documentUrl?.('an/id', true)).toBe('/demo/api/document/an%2Fid/preview');
});

test('production keeps RU/EN/ES preferences and the existing pseudolocale without public routing', async () => {
  const router = createAppRouter(createMemoryHistory());
  const app = createProductionRuntime(router);
  expect(app.readOnly).toBe(false);
  expect(app.intakeComponent).toBeDefined();
  expect(app.locales.some((item) => item.code === 'es')).toBe(true);
  const registry = localeRegistry as LocaleSpec[];
  registry.push({ code: 'qps', name: 'Pseudo (test)', intl: 'en-GB', direction: 'ltr' });
  try {
    const pseudo = createProductionRuntime(router);
    localStorage.setItem(localeStorageKey, 'qps');
    expect(pseudo.initialLocale()).toBe('qps');
    expect(pseudo.locales.some((item) => item.code === 'qps')).toBe(true);
  } finally {
    registry.pop();
  }
  localStorage.setItem(localeStorageKey, 'es');
  expect(app.initialLocale()).toBe('es');
  app.saveLocale('en');
  expect(localStorage.getItem(localeStorageKey)).toBe('en');
  expect(app.href).toBeUndefined();
});
