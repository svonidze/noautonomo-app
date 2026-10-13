import { afterEach, describe, expect, it } from 'vitest';
import { createWebHashHistory } from 'vue-router';
import { createAppRouter } from '../../src/shell/create-router.ts';
import { publicHref, publicLocale, savePublicLocale } from '../../src/shell/public-runtime.ts';
import { viewHref, documentHref, type ViewServices } from '../../src/vue/services.ts';

afterEach(() => window.history.replaceState({}, '', '/'));
describe('public route and language policy', () => {
  it('defaults to English independently of browser preference and storage', () => {
    expect(publicLocale('')).toBe('en');
    expect(publicLocale('?lang=unknown')).toBe('en');
    expect(publicLocale('?lang=es')).toBe('es');
    expect(publicLocale('?lang=ru')).toBe('ru');
  });
  it('keeps the route, filters and Vue history ownership when switching language', async () => {
    window.history.replaceState(
      { contactGlobalPeriod: '2026-Q2' },
      '',
      '/demo/?lang=en#/expenses?period=2026-Q3&q=Atlas',
    );
    const router = createAppRouter(createWebHashHistory('/demo/'));
    await router.push('/expenses?period=2026-Q3&q=Atlas');
    const before = window.history.state;
    savePublicLocale('es');
    expect(window.location.search).toBe('?lang=es');
    expect(window.location.hash).toBe('#/expenses?period=2026-Q3&q=Atlas');
    expect(window.history.state).toEqual(before);
    await router.push('/contacts?period=2026-Q2');
    expect(window.location.search).toBe('?lang=es');
    expect(window.location.hash).toBe('#/contacts?period=2026-Q2');
    router.options.history.destroy();
  });
  it('gives ordinary, new-tab and document links an explicit public destination', () => {
    const services: ViewServices = {
      request: async () => ({}),
      navigate() {},
      href: (url) => publicHref(url, 'ru'),
      documentUrl: (id) => `/demo/api/document/${id}/content`,
    };
    expect(viewHref(services, '/review/abc?period=2026-Q3&returnTo=%2Fcontacts')).toBe(
      '/demo/?lang=ru#/review/abc?period=2026-Q3&returnTo=%2Fcontacts',
    );
    expect(documentHref(services, 'sample')).toBe('/demo/api/document/sample/content');
    expect(viewHref(undefined, '/settings')).toBe('/settings');
    expect(documentHref(undefined, 'sample', true)).toBe('/api/document/sample/preview');
    expect(() => publicHref('https://elsewhere.invalid/account', 'en')).toThrow(
      'External public application URL',
    );
  });
});
